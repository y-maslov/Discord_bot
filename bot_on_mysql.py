from config import settings
import discord
from discord.ext import commands
from discord.ext import tasks
import datetime
from mysql.connector import connect, Error
import challonge



challonge.set_credentials(settings['challonge_name'], settings['challonge_api_key'])
bot = discord.Client()
bot = commands.Bot(command_prefix = settings['bot_prefix'])



@bot.event      # вывод в консоль имени бота 
async def on_ready():
    print('We have logged in as {0.user}'.format(bot))
    check_in_vote.start()
    tournament_start.start()
    send_opposite_player_info.start()

    print(datetime.datetime.now())
    
################################################################################ создание турнира и регистрация


@tasks.loop(seconds=1.0)  # Отправка формы с реакцией в чат check_in
async def check_in_vote():
    datetime_now = datetime.datetime.now()
    try:                            # Поиск турниров с часом до начала и меньше в tournaments_data_sheet в SQL
        with connect(
            host = settings["host"],
            user = settings["user"],
            password = settings["password"],
            database = settings["database"]
        ) as connection:
            get_tournaments_with_check_in_query = ("SELECT * FROM tournaments_data_sheet WHERE TIMEDIFF(zh_datetime,  '" + str(datetime_now) + "') > 0 AND TIMEDIFF(zh_datetime, '" + str(datetime.datetime.now() + datetime.timedelta(0, 3600)) + "') <= 0 AND check_in_id IS NULL;")
            with connection.cursor() as cursor:
                cursor.execute(get_tournaments_with_check_in_query)
                all_tournaments_to_execute = cursor.fetchall()
                for tournament_data in all_tournaments_to_execute:
                    ctx = await bot.fetch_channel(settings['check_in_chat_id'])                             # в кавычках  id чата для анонса (отправка сообщения в определенный чат  https://qna.habr.com/q/792859)
                    check_in_form = await ctx.send('До закрытия регистрации на ' + tournament_data[1] + ' меньше часа. Всем check_inится!')
                    await check_in_form.add_reaction('✅') 
                    update_query = ("UPDATE tournaments_data_sheet SET check_in_id = " + str(check_in_form.id) + " WHERE announcement_id = " + str(tournament_data[0]) + ";")
                    cursor.execute(update_query)
                    print("Check_in started")

                connection.commit()
    except Error as e:
        print(e)   

    

@bot.command() # создание турнира
async def new_tournament(ctx, tournament_name, tournament_description, zh_year, zh_mon, zh_mday, zh_hour, zh_min, tournament_type):       # ввод времени в виде 2022 7 17 20 00
    ctx = await bot.fetch_channel(settings['announcement_chat_id'])                             # в кавычках  id чата для анонса (отправка сообщения в определенный чат  https://qna.habr.com/q/792859)
    announcement = await ctx.send(tournament_name + '\n' + tournament_description +'\n' + zh_hour + '.' + zh_min + ' ' + zh_mday + '.' + zh_mon + '.' + zh_year)
    await announcement.add_reaction('✅') 
   


    try:                            # Создание таблицы турнира в базе данных турниров в SQL
        with connect(
            host = settings["host"],
            user = settings["user"],
            password = settings["password"],
            database = settings["database"]
        ) as connection:
            create_player_data_table_query = """
            CREATE TABLE """ + """`""" + str(announcement.id) + """`""" + """
            (
                discord_user_id BIGINT  PRIMARY KEY,
                `name` VARCHAR(1000),
                place VARCHAR(1000),
                connect_data VARCHAR(1000),
                check_in TINYINT(1),
                challonge_player_id BIGINT,
                current_match_id BIGINT,
                challonge_opposite_player_id BIGINT DEFAULT 0,
                get_opposite_player_info TINYINT(1) DEFAULT 0,
                player_win TINYINT(1) DEFAULT 0
                
            )
            """
            with connection.cursor() as cursor:
                cursor.execute(create_player_data_table_query)
                connection.commit()
    except Error as e:
        print(e)


    tournament_datetime = datetime.datetime(int(zh_year), int(zh_mon), int(zh_mday), int(zh_hour),int(zh_min), 0)  # сохраняем время турнира в datetime 
    print(tournament_datetime)

    challonge_tournament = challonge.tournaments.create(tournament_name, announcement.id, tournament_type) # создание турнира на challonge
    

    try:                            # Внесение данных турнира в таблицу tournaments_data_sheet в SQL
        with connect(
            host = settings["host"],
            user = settings["user"],
            password = settings["password"],
            database = settings["database"]

        ) as connection:
            insert_tournament_data_query = ("INSERT INTO tournaments_data_sheet" 
            "(announcement_id, tournament_name, tournament_description, zh_datetime, challonge_tournament_id) "
            "VALUES (%s, %s, %s, %s, %s);")

            tournament_data = (announcement.id, tournament_name, tournament_description, tournament_datetime, challonge_tournament["id"])

            with connection.cursor() as cursor:
                cursor.execute(insert_tournament_data_query, tournament_data)
                connection.commit()
    except Error as e:
        print(e)

    print("Here is new tournament")



@bot.command() # удаление турнира
async def delete_tournament(ctx, tournament_id):
    
    try:                            # удаление таблицы с данными игроков из базы данных 'tournaments_info' в SQL
        with connect(
            host = settings["host"],
            user = settings["user"],
            password = settings["password"],
            database = settings["database"]

        ) as connection:
            delete_player_data_query = ("DROP TABLE `" + str(tournament_id) + "`;") 
            with connection.cursor() as cursor:
                cursor.execute(delete_player_data_query)
                connection.commit()

    except Error as e:
        print(e)

    

    try:                            # удаление данных турнира из  'tournaments_data_sheet' в SQL
        with connect(
            host = settings["host"],
            user = settings["user"],
            password = settings["password"],
            database = settings["database"]

        ) as connection:
            delete_tournament_data_query = ("DELETE FROM tournaments_data_sheet WHERE announcement_id =" + str(tournament_id) + ";") 
            with connection.cursor() as cursor:
                cursor.execute(delete_tournament_data_query)
                connection.commit()

    except Error as e:
        print(e)

    
    ctx = await bot.fetch_channel(settings['announcement_chat_id'])
    tournament = await ctx.fetch_message(int(tournament_id))
    await tournament.delete()
    challonge.tournaments.destroy(tournament_id)
    print("Tournament deleted")
   


@bot.listen() # регистрация игрока (опрос в личных сообщениях)
async def on_raw_reaction_add(reaction): # уточнить про  Intents.messages
    
    if  reaction.channel_id != settings['announcement_chat_id'] or reaction.user_id == settings['bot_id']:
        return

    def check(m):       # проверка, что ответ не дан ботом
        return m.author != bot.user and  m.author.id == reaction.user_id        # сделать так, чтобы ответ читался только из лс
    user = reaction.member

    


    try:                            # получение даты начала турнира из 'tournaments_data_sheet' в SQL
        with connect(
            host = settings["host"],
            user = settings["user"],
            password = settings["password"],
            database = settings["database"]

        ) as connection:
            tournament_datetime_query = ("SELECT zh_datetime FROM tournaments_data_sheet WHERE announcement_id = " + str(reaction.message_id) + ";") 
            with connection.cursor() as cursor:
                cursor.execute(tournament_datetime_query)
                tournament_datetime_raw = cursor.fetchone()
                tournament_datetime = tournament_datetime_raw[0]

                connection.commit()

    except Error as e:
        print(e)

    

    if  tournament_datetime <= datetime.datetime.now(): # Проверка для того, чтобы бот не пытался сам себе отправить форму для регистрации, чтобы форма не кидалась на случайные реакции к случайным сообщениям, чтобы форма не кидалась после начала турнира . В настройках чата для анонса нужно запретить добавлять реакции обычным пользователям
        print("Player is not allowed")
        return 
    
  
    await user.send("Ваше ФИО")
    answer = await bot.wait_for('message', check = check)
    name = answer.content
    await user.send("Ваше место учёбы")
    answer  = await bot.wait_for('message', check = check)
    place = answer.content
    await user.send("Что-нибудь для связи (ссылка в вк например)")
    answer  = await bot.wait_for('message', check = check)
    connect_data = answer.content

    await user.send("Спасибо за регистрацию")
    


    try:                            # Внесение данных игрока в таблицу игроков турнира в SQL
        with connect(
            host = settings["host"],
            user = settings["user"],
            password = settings["password"],
            database = settings["database"]

        ) as connection:
            insert_player_data_query = ("INSERT INTO `%s`" 
            "(discord_user_id, `name`, place, connect_data) "
            "VALUES (%s, %s, %s, %s);")

            player_data = (reaction.message_id, reaction.user_id, name, place, connect_data)

            with connection.cursor() as cursor:
                cursor.execute(insert_player_data_query, player_data)
                connection.commit()
                print("Here is new player")
    except Error as e:
        print(e)



@bot.listen() # удаление регистрации игрока (если тот убирает реакцию)
async def on_raw_reaction_remove(reaction): 
    if  reaction.channel_id != settings['announcement_chat_id']: # Проверка для того, чтобы бот не удалял регистрацию, если реакция удалена не на сообщении с регистрацией
        return 

    try:                            # удаление данных игрока из таблицы игроков турнира в SQL
        with connect(
            host = settings["host"],
            user = settings["user"],
            password = settings["password"],
            database = settings["database"]

        ) as connection:
            delete_player_data_query = ("DELETE FROM `" + str(reaction.message_id) + "` WHERE discord_user_id =" + str(reaction.user_id) + ";") 
            with connection.cursor() as cursor:
                cursor.execute(delete_player_data_query)
                connection.commit()

    except Error as e:
        print(e)

    print("Player deleted")



@bot.listen('on_raw_reaction_add') # проверка регистрации (check_in) игрока (опрос через эмоции)    
async def on_check_in_raw_reaction_add(reaction): # уточнить про  Intents.messages
    if  reaction.channel_id != settings['check_in_chat_id'] or reaction.user_id == settings['bot_id']:
        return
    
    try:                            
        with connect(
            host = settings["host"],
            user = settings["user"],
            password = settings["password"],
            database = settings["database"]

        ) as connection:
            get_tourament_id_query = ("SELECT announcement_id, zh_datetime FROM tournaments_data_sheet WHERE check_in_id = " + str(reaction.message_id) + ";") 
            with connection.cursor() as cursor:
                cursor.execute(get_tourament_id_query)
                tournament_raw = cursor.fetchone()
                if tournament_raw[1] < datetime.datetime.now():
                    print('Late')
                    return
                else:
                    update_player_check_in_status_query = ("UPDATE `" + str(tournament_raw[0]) + "` SET check_in = 1 WHERE discord_user_id = " + str(reaction.user_id) + ";")
                    cursor.execute(update_player_check_in_status_query)
                    connection.commit()
                    print('check_in status updated')

    except Error as e:
        print(e)



@bot.listen('on_raw_reaction_remove') # удаление (check_in) игрока (если тот убирает реакцию)
async def on_check_in_raw_reaction_remove(reaction): 
    if  reaction.channel_id != settings['check_in_chat_id'] or reaction.user_id == settings['bot_id']: # Проверка для того, чтобы бот не удалял check_in, если реакция удалена не на сообщении с check_in
        return 

    try:                            
        with connect(
            host = settings["host"],
            user = settings["user"],
            password = settings["password"],
            database = settings["database"]

        ) as connection:
            get_tourament_id_query = ("SELECT announcement_id, zh_datetime FROM tournaments_data_sheet WHERE check_in_id = " + str(reaction.message_id) + ";") 
            with connection.cursor() as cursor:
                cursor.execute(get_tourament_id_query)
                tournament_raw = cursor.fetchone()
                if tournament_raw[1] < datetime.datetime.now():
                    print('Late')
                    return
                else:
                    update_player_check_in_status_query = ("UPDATE `" + str(tournament_raw[0]) + "` SET check_in = 0 WHERE discord_user_id = " + str(reaction.user_id) + ";")
                    cursor.execute(update_player_check_in_status_query)
                    connection.commit()
                    print('check_in status updated')

    except Error as e:
        print(e)

############################################################################### начало и процесс турнира
# функция для вывода итога матча
async def send_match_info(challonge_match_id):
    tournament = challonge.tournaments.show(str(challonge_match_id))
    ctx = await bot.fetch_channel(settings['tournaments_info_update_id'])
    await ctx.send(tournament["full_challonge_url"])
    print(tournament["full_challonge_url"])


@tasks.loop(seconds=1.0)  # Начало турнира
async def tournament_start():
    datetime_now = datetime.datetime.now()
    
    try:                            # Поиск турниров с истекшей регистрацией  tournaments_data_sheet в SQL
        with connect(
            host = settings["host"],
            user = settings["user"],
            password = settings["password"],
            database = settings["database"]
        ) as connection:
            get_tournaments_to_start_query = ("SELECT * FROM tournaments_data_sheet WHERE (TIMEDIFF(zh_datetime, '" + str(datetime.datetime.now()) + "') <= 0) AND (challonge_start IS NULL);")
            with connection.cursor() as cursor:
                cursor.execute(get_tournaments_to_start_query)
                all_tournaments_to_execute = cursor.fetchall()
                # print(all_tournaments_to_execute)
                for tournament_data in all_tournaments_to_execute: 
                 
                    get_checked_players_query = ("SELECT * FROM `" + str(tournament_data[0]) + "` WHERE check_in = 1;")  #добавление игроков с чек инами в challonge
                    cursor.execute(get_checked_players_query)
                    all_players_to_challonge = cursor.fetchall()
                    player_cnt = 0
                    for player in all_players_to_challonge:   
                        challonge_player = challonge.participants.create(tournament_data[5], player[1])
                        player_cnt += 1 
                        update_challonge_player_id_query = ("UPDATE `" + str(tournament_data[0]) + "` SET challonge_player_id = " + str(challonge_player["id"]) + " WHERE discord_user_id = " + str(player[0]) + ";") 
                        cursor.execute(update_challonge_player_id_query)
                        connection.commit()
                        print(challonge_player["id"])
                    if player_cnt >= 2:                                          # проверка на минимальное число игроков (иначе турнир не начать)
                        challonge.participants.randomize(tournament_data[5])     # жеребьевка игроков в challonge
                        challonge.tournaments.start(tournament_data[5])
                        print("tournament started")


                        matches_info = challonge.matches.index(tournament_data[5])  
                        for one_match_info in matches_info:                     # запись id матча и id оппонента (если есть) в строки игроков
                            if str(one_match_info['player1_id']) != "None" and str(one_match_info['player2_id']) != "None":
                                update_challonge_match_id_query = ("UPDATE `" + str(tournament_data[0]) + "` SET current_match_id = " + str(one_match_info['id']) + " WHERE challonge_player_id = " + str(one_match_info['player1_id']) + " OR challonge_player_id = " + str(one_match_info['player2_id']) + ";") 
                                cursor.execute(update_challonge_match_id_query)
                                connection.commit()
                                update_challonge_opposite_player1_id_query = ("UPDATE `" + str(tournament_data[0]) + "` SET challonge_opposite_player_id = " + str(one_match_info['player1_id']) + " WHERE challonge_player_id = " + str(one_match_info['player2_id']) + ";")
                                cursor.execute(update_challonge_opposite_player1_id_query)
                                connection.commit()
                                update_challonge_opposite_player2_id_query = ("UPDATE `" + str(tournament_data[0]) + "` SET challonge_opposite_player_id = " + str(one_match_info['player2_id']) + " WHERE challonge_player_id = " + str(one_match_info['player1_id']) + ";")
                                cursor.execute(update_challonge_opposite_player2_id_query)
                                connection.commit()

                            elif str(one_match_info['player1_id']) != "None" and str(one_match_info['player2_id']) == "None":
                                update_challonge_match_id_query = ("UPDATE `" + str(tournament_data[0]) + "` SET current_match_id = " + str(one_match_info['id']) + " WHERE challonge_player_id = " + str(one_match_info['player1_id']) + ";") 
                                cursor.execute(update_challonge_match_id_query)
                                connection.commit()
                            elif str(one_match_info['player1_id']) == "None" and str(one_match_info['player2_id']) != "None":
                                update_challonge_match_id_query = ("UPDATE `" + str(tournament_data[0]) + "` SET current_match_id = " + str(one_match_info['id']) + " WHERE challonge_player_id = " + str(one_match_info['player2_id']) + ";") 
                                cursor.execute(update_challonge_match_id_query)
                                connection.commit()

                    else:
                        print("Less than 2")

                        
                    update_challonge_status_query = ("UPDATE tournaments_data_sheet SET challonge_start = 1 WHERE announcement_id = " + str(tournament_data[0]) + ";") 
                    cursor.execute(update_challonge_status_query)
                    print("tournament executed")
                        
                    
                connection.commit()
    except Error as e:
        print(e)   


@tasks.loop(seconds=2.0) # Отправка информации игрока противнику
async def send_opposite_player_info():
    try:                           
        with connect(
            host = settings["host"],
            user = settings["user"],
            password = settings["password"],
            database = settings["database"]
        ) as connection:
            get_started_tournaments_query = ("SELECT * FROM tournaments_data_sheet WHERE challonge_start = 1;")   # получение начавшихся турниров
            with connection.cursor() as cursor:
                cursor.execute( get_started_tournaments_query)
                all_started_tournaments = cursor.fetchall()         # двумерный массив из начавшихся турниров
                for started_tournament in all_started_tournaments:
                    get_players_without_info_query = ("SELECT * FROM `" + str(started_tournament[0]) + "` WHERE get_opposite_player_info = 0;")
                    cursor.execute(get_players_without_info_query)
                    players_without_info = cursor.fetchall()         # все игроки у которых get_opposite_player_info == 0
                    for single_player_without_info in players_without_info:
                        if (single_player_without_info[7] != 0):  # проверка что challonge_opposite_player_id не равен 0
                            get_opposite_player_info_query = ("SELECT * FROM `" + str(started_tournament[0]) + "` WHERE challonge_player_id = " + str(single_player_without_info[7]) + ";")
                            cursor.execute( get_opposite_player_info_query)
                            opposite_player_info =  cursor.fetchall()
                            user =  await bot.fetch_user(single_player_without_info[0])
                            
                            await user.send('Вот ссылка на вашего соперника: ' + str(opposite_player_info[0][3]) + ' . После матча скопируйте текст в кавычках и отправьте боту, заменив пароль и ссылку. Удачи!')   
                            await user.send('"$match_update ' + str(started_tournament[0]) + ' 1 http://Linkwithevidence.com"   при победе')
                            await user.send('"$match_update ' + str(started_tournament[0]) + ' 2 http://Linkwithevidence.com" при поражении.')
                            status_update_query = ("UPDATE `" + str(started_tournament[0]) + "` SET get_opposite_player_info = 1 WHERE discord_user_id = " + str(single_player_without_info[0]) + ";")
                            cursor.execute(status_update_query)
                            connection.commit()
                connection.commit()
    except Error as e:
        print(e)    



@bot.command()
async def match_update(ctx, tournament_id, wl_status, evidence_link): # Прием информации о матче, обновление сетки, обновление таблицы
    # "0" - матч в процессе, "1" - победа игрока, "2" - поражение игрока
    try:                            
        with connect(
            host = settings["host"],
            user = settings["user"],
            password = settings["password"],
            database = settings["database"]

        ) as connection:
            player_status_update_query = ("UPDATE `" + str(tournament_id) + "` SET player_win = " + str(wl_status) + " WHERE discord_user_id = " + str(ctx.message.author.id))  
            player_self_check_and_challonge_info_query = ("SELECT player_win, current_match_id, challonge_player_id, challonge_opposite_player_id FROM `" + str(tournament_id) + "` WHERE discord_user_id = " + str(ctx.message.author.id))
        
            with connection.cursor() as cursor:
                cursor.execute(player_status_update_query) # Обновление player_win с статусами матча
                print("player status updated")

                cursor.execute(player_self_check_and_challonge_info_query) # Получаем значение player_win, чтобы игрок проверил отправку, и current_match_id для проверки значения другого игрока 
                player_win_and_challonge_info = cursor.fetchall()
                await ctx.message.author.send('Статус вашего матча обновлён. Но, пожалуйста, проверьте, что эта цифра "' + str(player_win_and_challonge_info[0][0])  + '" совпадает с той, что идет перед ссылкой на доказательство, и является 1 или 2. Если они не совпадают,  пишите админам.')

                other_player_win_and_discord_id_query = ("SELECT player_win, discord_user_id FROM `" + str(tournament_id) + "` WHERE discord_user_id != " + str(ctx.message.author.id) + " AND current_match_id = " + str(player_win_and_challonge_info[0][1]))
                cursor.execute(other_player_win_and_discord_id_query)
                other_player_win_and_discord_id = cursor.fetchall()
                connection.commit()
                if int(other_player_win_and_discord_id[0][0]) == 0 or int(player_win_and_challonge_info[0][0]) == 0:  # Сравниваем player_win для обновления таблицы и проверки
                    print("match is going")

               
                elif int(other_player_win_and_discord_id[0][0]) == int(player_win_and_challonge_info[0][0]):
                    print("need admin")
                    chat = await bot.fetch_channel(settings['issues_with_players_chat_id'])
                    await chat.send("Проблема у людей с discord_user_id " + str(other_player_win_and_discord_id[0][1]) + " и " + str(ctx.message.author.id) + ", где announcement_id (он же tournament_id) " + str(tournament_id))
                    
                else: # Если всё хорошо, то обновляем сетку, и обновляем матчи
                    if int(other_player_win_and_discord_id[0][0]) == 2 and int(player_win_and_challonge_info[0][0]) == 1:
                        print("other player lose")
                        challonge.matches.update(str(tournament_id),  player_win_and_challonge_info[0][1], winner_id =  player_win_and_challonge_info[0][2], scores_csv = '1-1')
                        
                    elif int(other_player_win_and_discord_id[0][0]) == 1 and int(player_win_and_challonge_info[0][0]) == 2:
                        print("other player win")
                        challonge.matches.update(str(tournament_id),  player_win_and_challonge_info[0][1], winner_id =  player_win_and_challonge_info[0][3], scores_csv = '1-1')
                    

                    # Этот код берет и синхронизирует данные сетки и таблиц в БД
                    get_tournament_info_query = ("SELECT * FROM tournaments_data_sheet WHERE announcement_id = " + str(tournament_id) + ";")
                    cursor.execute(get_tournament_info_query)
                    tournament_data = cursor.fetchone()
                    zero_challonge_opposite_player_id_query = ("UPDATE `" + str(tournament_data[0]) + "` SET challonge_opposite_player_id = 0;")
                    cursor.execute(zero_challonge_opposite_player_id_query)
                    connection.commit()
                    matches_info = challonge.matches.index(tournament_data[5]) 
                    for one_match_info in matches_info:
                        if str(one_match_info['winner_id']) == "None" and str(one_match_info['player1_id']) != "None" and str(one_match_info['player2_id']) != "None":
                            update_challonge_match_id_query = ("UPDATE `" + str(tournament_data[0]) + "` SET current_match_id = " + str(one_match_info['id']) + " WHERE challonge_player_id = " + str(one_match_info['player1_id']) + " OR challonge_player_id = " + str(one_match_info['player2_id']) + ";") 
                            cursor.execute(update_challonge_match_id_query)
                            connection.commit()
                            update_challonge_opposite_player1_id_query = ("UPDATE `" + str(tournament_data[0]) + "` SET challonge_opposite_player_id = " + str(one_match_info['player1_id']) + " WHERE challonge_player_id = " + str(one_match_info['player2_id']) + ";")
                            cursor.execute(update_challonge_opposite_player1_id_query)
                            connection.commit()
                            update_challonge_opposite_player2_id_query = ("UPDATE `" + str(tournament_data[0]) + "` SET challonge_opposite_player_id = " + str(one_match_info['player2_id']) + " WHERE challonge_player_id = " + str(one_match_info['player1_id']) + ";")
                            cursor.execute(update_challonge_opposite_player2_id_query)
                            connection.commit()
                        elif str(one_match_info['winner_id']) == "None" and str(one_match_info['player1_id']) != "None" and str(one_match_info['player2_id']) == "None":
                            update_challonge_match_id_query = ("UPDATE `" + str(tournament_data[0]) + "` SET current_match_id = " + str(one_match_info['id']) + " WHERE challonge_player_id = " + str(one_match_info['player1_id']) + ";") 
                            update_challonge_opposite_player_id_query = ("UPDATE `" + str(tournament_data[0]) + "` SET challonge_opposite_player_id = 0 WHERE challonge_player_id = " + str(one_match_info['player1_id']) + ";")
                            cursor.execute(update_challonge_opposite_player_id_query)
                            cursor.execute(update_challonge_match_id_query)
                            connection.commit()
                        elif str(one_match_info['winner_id']) == "None" and str(one_match_info['player1_id']) == "None" and str(one_match_info['player2_id']) != "None":
                            update_challonge_match_id_query = ("UPDATE `" + str(tournament_data[0]) + "` SET current_match_id = " + str(one_match_info['id']) + " WHERE challonge_player_id = " + str(one_match_info['player2_id']) + ";") 
                            update_challonge_opposite_player_id_query = ("UPDATE `" + str(tournament_data[0]) + "` SET challonge_opposite_player_id = 0 WHERE challonge_player_id = " + str(one_match_info['player2_id']) + ";")
                            cursor.execute(update_challonge_opposite_player_id_query)
                            cursor.execute(update_challonge_match_id_query)
                            connection.commit()

                    # Сброс get_opposite_player_info и player_win у игроков, сыгравших матч
                    update_player_win_and_get_opposite_player_info_query = ("UPDATE `" + str(tournament_data[0]) + "` SET get_opposite_player_info = 0, player_win = 0 WHERE discord_user_id = " + str(ctx.message.author.id) + " OR discord_user_id = " + str(other_player_win_and_discord_id[0][1]) + ";")
                    cursor.execute(update_player_win_and_get_opposite_player_info_query)
                    connection.commit()
                    
                    #кидаем обновленную сетку
                    send_match_info()

    except Error as e:
        print(e)
    



bot.run(settings['bot_token']) # Bot token 
  