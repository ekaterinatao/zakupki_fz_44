import zipfile
import xmlschema
from io import BytesIO
from psycopg2 import sql
import os
import re
import json
import uuid
from tqdm import tqdm
import numpy as np
import signal
import sys
import atexit


from connection import create_conn_postgre, create_conn_psyco
from func import preproc_data, extract_region_and_year

from collections import OrderedDict


def get_region_contract_path(year_start: int, year_end: str, parent_folder: str, doc_type: str) -> list[str]:
    """ Функция для формирования списка путей к zip архивам.
        Возможно сформировать путь к любому xml файлу из директории: /home/user/ZAKUPKI/free/fcs_regions/
    
        year_start: int - год, начиная с которого планируется парсинг
        year_end: int - год окончания парсинга, включительно
        parent_folder: str - часть пути содержит: 'директория внутри региона' 
                       например, для контрактов: 'contracts'
        doc_type: str - часть пути содержит: 'тип xml-документа' 
                       например, для контрактов: 'contract'
    """
    try:
        print('... get zip paths')
        conn = create_conn_psyco()
        sftp_client = conn.open_sftp()
        regions = sftp_client.listdir('/home/user/ZAKUPKI/free/fcs_regions')
        reg = []

        years_list = np.arange(year_start, year_end + 1)
        for name in regions:
            if re.findall(r'^[A-Z].+', name) and name not in ['PG-PZ','ERUZ']:
                for y in years_list:
                    reg.append(f"/home/user/ZAUPKI/free/fcs_regions/{name}/{parent_folder}/{doc_type}_{name}_{y}*.xml.zip")
                reg.append(f"/home/user/ZAKUPKI/free/fcs_regions/{name}/contracts/currMonth/{doc_type}_{name}_2024*.xml.zip")
                reg.append(f"/home/user/ZAKUPKI/free/fcs_regions/{name}/contracts/prevMonth/{doc_type}_{name}_2024*.xml.zip")
            elif 'fcs_undefined' in name:
                reg.append(f"/home/user/ZAKUPKI/free/fcs_regions/{name}/{parent_folder}/{doc_type}_*.xml.zip")
            elif 'prevMonth' in name:
                reg.append(f"/home/user/ZAKUPKI/free/fcs_regions/{name}/{parent_folder}/{doc_type}_*.xml.zip")
                reg.append(f"/home/user/ZAKUPKI/free/fcs_regions/{name}/Altaj_Resp/{parent_folder}/{doc_type}_*.xml.zip")

        reg_sorted = sorted(reg, key=extract_region_and_year)

        return reg_sorted

    except Exception as ex:
        print(f"\n{ex}")
    finally:
        conn.close()


def parse_xml(remote_zip_path: list[str], schema: xmlschema) -> tuple[list[dict]]:
    """ Парсинг нескольких архивов.
        Функция возвращает список xml-файлов и список ошибок.
    
        remote_zip_path: list[str] - список путей к zip архивам, каждый путь может быть с регуляркой
                пример: '/home/user/ZAKUPKI/free/fcs_regions/Moskva/contracts/contract_Moskva_2020*.xml.zip'
        schema: xmlschema - схема, для всех файлов подходит: 'scheme_15_1_i1/fcsExport.xsd'
    """

    try:
        conn = create_conn_psyco()
        main, err = [], []
        # --- for several zip files ---
        for path in remote_zip_path:
            print(path)
            stdin, stdout, stderr = conn.exec_command(f'ls {path}')
            zip_files = stdout.read().decode().splitlines()
            schemas_lst = []
            err_lst = []

            if zip_files:
                count = 0 # для проверки нескольких файлов из каждого региона
                rand_vals = np.random.randint(len(zip_files), size=5) # для проверки нескольких файлов из каждого региона

                for zip_path in tqdm(zip_files):
                    if (count % 1000 == 0) | (count in rand_vals): # для проверки нескольких файлов из каждого региона
                        try:
                            with conn.open_sftp() as sftp:
                                with sftp.file(zip_path, mode='rb') as remote_file:
                                    zip_file_data = BytesIO(remote_file.read())
                                    zf = zipfile.ZipFile(zip_file_data, 'r')
                                    for file_path in zf.namelist():
                                        if file_path.endswith('.xml'):
                                            with zf.open(file_path) as f:
                                                file = f.read()
                                                parsed_file = schema.to_dict(file, validation='lax')
                                                if parsed_file[0] and count % 500 == 0:                 # для проверки, сохраняем не все файлы
                                                    schemas_lst.append(parsed_file[0])
                                                if parsed_file[1]:
                                                    err_lst.append({
                                                        'id': str(uuid.uuid4()),
                                                        'error': parsed_file[1],
                                                        'path': zip_path,
                                                        'child_path': file_path
                                                    })
                        except: pass
                    count += 1 # для проверки нескольких файлов из каждого региона
            print(len(schemas_lst), len(err_lst))
            main.extend(schemas_lst)
            err.extend(err_lst)

        return main, err

    except Exception as ex:
        print(f"\n{ex}")
    finally:
        conn.close()

        
class RecursiveProcessor:
    """Класс для парсинга словаря в связанные таблицы"""
    def __init__(self):
        self.global_list_tables = {} # Глобальный словарь с таблицами

    def cicle_parsing(self, data: list[dict], past_table_name: str, past_key_name: str, uid: uuid, parent_uid: str=None):

        """ Рекурсивный метод для парсинга словарей в связанные таблицы

        data: list[dict] - список словарей, полученный при парсинге архивов c xml-файлами (функция parse_xml)
        past_name: str - тэг xml-файлов
        uid: uuid - первичный ключ для таблицы
        parent_uid: str=None - внешний ключ для таблицы
        """

        if isinstance(data, dict):
            sub_dict = {'uid': uid}  # Список для формирования заносящегося списка
            if parent_uid:  # Если есть родитель, добавляем связь
                sub_dict['parent_uid'] = parent_uid

            for key in data:

                # в разных версиях контрактов есть префиксы 'ns[1-9]', удаляем эти префиксы             
                current_table_name = (past_table_name + '.' + re.sub(r'ns\d:', '', key)).strip('.').strip()
                current_key_name = (past_key_name + '.' + re.sub(r'ns\d:', '', key)).strip('.').strip()

                if isinstance(data[key], dict):                
                    sub_dict.update(self.cicle_parsing(data[key], past_table_name, current_key_name, uid, parent_uid)) 
                elif isinstance(data[key], (tuple, list)): 

                    if current_table_name not in self.global_list_tables:
                        self.global_list_tables[current_table_name] = []
                    self.cicle_parsing(data[key], current_table_name, '', str(uuid.uuid4()), uid)
                else:
                    sub_dict[current_key_name] = data[key]
            return sub_dict

        elif isinstance(data, (list, tuple)):

            for el in data:
                if isinstance(el, dict):
                    nested_uid = str(uuid.uuid4())
                    if past_table_name not in self.global_list_tables:
                        self.global_list_tables[past_table_name] = []
                    self.global_list_tables[past_table_name].append(
                        self.cicle_parsing(el, past_table_name, past_key_name, nested_uid, parent_uid)
                    )
                elif isinstance(el, (list, tuple)):
                    print('pizdec')
                else:
                    self.global_list_tables[past_table_name].append({'uid': uid, 'parent_uid': parent_uid, 'VALUE': el})


def create_table(list_tables: list[dict], table_name: str, schema_name: str='fz_44'):
    "Создание таблицы в БД"
    conn = None
    try:
        conn = create_conn_postgre()
        conn.autocommit = False  # Используем явные транзакции

        df, df_types = preproc_data(list_tables, table_name, schema_name)

        with conn.cursor() as cur:
            query = sql.SQL("CREATE TABLE IF NOT EXISTS {schema}.{table} ({fields})").format(
                schema=sql.Identifier(schema_name),
                table=sql.Identifier(table_name),
                fields=sql.SQL(', ').join([
                    sql.SQL("{} {}").format(
                        sql.Identifier(col_name),
                        sql.SQL(col_type)
                    ) for col_name, col_type in zip(df.columns, df_types)
                ])
            )
            cur.execute(query)
            print(f"Таблица {table_name} создана")

            conn.commit()

    except Exception as e:
        print(f"Ошибка: {e}")
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()


def check_table(table_name: str, cur) -> bool:
    "Проверка наличия заданной таблицы в БД"
    cur.execute("""
        SELECT EXISTS (
            SELECT FROM information_schema.tables 
            WHERE table_schema = 'fz_44' AND table_name = %s
        );
    """, (table_name,))
    return cur.fetchone()[0]


def mark_zip(zip_path: str, success: bool, connection, cur):
    "Записывает в таблицу БД путь и текущее состояние zip файла"
    cur.execute("SELECT sucsess FROM fz_44.zip_path WHERE path = %s", (zip_path,))
    result = cur.fetchone()

    if result is None:
        cur.execute(
            "INSERT INTO fz_44.zip_path (path, sucsess) VALUES (%s, %s)",
            (zip_path, success)
        )
    elif result[0] is False and success is True:
        cur.execute(
            "UPDATE fz_44.zip_path SET sucsess = TRUE WHERE path = %s",
            (zip_path,)
        )
    connection.commit()


def mark_file(zip_path: str, file_path: str, success: bool, connection, cur):
    "Записывает в таблицу БД путь и текущее состояние xml файла"
    cur.execute("SELECT sucsess FROM fz_44.file_path WHERE path = %s", (file_path,))
    result = cur.fetchone()

    if result is None:
        cur.execute(
            "INSERT INTO fz_44.file_path (zip_path, path, sucsess) VALUES (%s, %s, %s)",
            (zip_path, file_path, success)
        )
    elif result[0] is False and success is True:
        cur.execute("""
            UPDATE fz_44.file_path SET sucsess = TRUE 
            WHERE zip_path = %s AND path = %s
            """, (zip_path, file_path,)
        )
    connection.commit()


def get_all_zip_path(cur) -> set:
    "Получение путей всех записанных zip файлов и состояний"
    cur.execute("SELECT path, sucsess FROM fz_44.zip_path ORDER BY path")
    rows = cur.fetchall()
    processed_zips = {r[0]: r[1] for r in rows} if rows else {}
    return processed_zips


def get_all_file_path_in_zip(zip_path: str, cur) -> set:
    "Получение путей всех записанных xml файлов и состояний"
    query = sql.SQL(""" 
        SELECT f.path, f.sucsess FROM fz_44.zip_path z
        JOIN fz_44.file_path f ON z.path = f.zip_path 
        WHERE f.zip_path = %s
        ORDER BY f.zip_path, f.path
    """)
    cur.execute(query, (zip_path,))
    rows = cur.fetchall()
    processed_files = {r[0]: r[1] for r in rows} if rows else {}
    return processed_files


# Глобальные соединения
psql_conn = None
ssh_conn = None

def close_all_connections():
    global psql_conn, ssh_conn
    if psql_conn:
        try:
            psql_conn.close()
            print("PostgreSQL connection closed.")
        except Exception:
            pass
    if ssh_conn:
        try:
            ssh_conn.close()
            print("SSH connection closed.")
        except Exception:
            pass

        
def handle_exit(signum, frame):
    "Закрытие соединений при прерывании скрипта с помощью ctrl+C "
    print(f"Получен сигнал {signum}. Завершение...")
    close_all_connections()
    sys.exit(0)

signal.signal(signal.SIGINT, handle_exit)
signal.signal(signal.SIGTERM, handle_exit)
atexit.register(close_all_connections)


def load_xml_to_bd(remote_zip_path: list[str], schema: xmlschema, schema_name: str='fz_44'):
    """ Парсинг нескольких архивов.
        Функция читает zip-архивы с сервера и записывает их в БД без сохранения локальных файлов.
    
        remote_zip_path: list[str] - список путей к zip архивам, каждый путь может быть с регуляркой
                пример: '/home/user/ZAKUPKI/free/fcs_regions/Moskva/contracts/contract_Moskva_2020*.xml.zip'
        schema: xmlschema - схема, для всех файлов подходит: 'scheme_15_1_i1/fcsExport.xsd'
    """
    global ssh_conn
    global psql_conn    

    with open('column.json', 'r') as f:
        ref_cols = json.load(f)

    columns_list = {top_key: list(nested_dict.values()) for top_key, nested_dict in ref_cols.items()}

    try:
        ssh_conn = create_conn_psyco()
        sftp = ssh_conn.open_sftp()
        psql_conn = create_conn_postgre()
        psql_cur = psql_conn.cursor()
        psql_conn.autocommit = False

        zip_path_in_bd = get_all_zip_path(cur=psql_cur)

        for path in remote_zip_path:
            print(path)
            try:
                stdin, stdout, stderr = ssh_conn.exec_command(f'ls {path}')
                stdout.channel.recv_exit_status()
                zip_files = sorted(stdout.read().decode().splitlines())
            except Exception as e:
                print(f"Ошибка при получении списка файлов: {e}")
                continue
            
            if not zip_files:
                print("Нет файлов по данному пути.")
                continue

            if zip_files:

                for zip_path in tqdm(zip_files, desc=f"Обработка zip в {path}"):
                    print(f"\n--- Обработка архива: {zip_path}")

                    if zip_path in zip_path_in_bd and zip_path_in_bd[zip_path]:
                        print(" -> Уже обработан.")
                        continue

                    mark_zip(zip_path, False, connection=psql_conn, cur=psql_cur)

                    try:
                        with sftp.file(zip_path, mode='rb') as remote_file:
                            zip_file_data = BytesIO(remote_file.read())
                        print('--- zip file успешно открыт')
                        zf = zipfile.ZipFile(zip_file_data, 'r')
                    except Exception as e:
                        print(f"--- Ошибка при чтении zip: {e}")
                        continue

                    file_list = sorted([fp for fp in zf.namelist() if fp.endswith('.xml')])
                    file_path_in_bd = get_all_file_path_in_zip(zip_path, cur=psql_cur)
                    all_files_success = True

                    for file_path in file_list:

                        if file_path in file_path_in_bd and file_path_in_bd[file_path]:
                            continue

                        try:
                            mark_file(zip_path, file_path, False, connection=psql_conn, cur=psql_cur)
                            file = zf.read(file_path)
                            parsed_file = schema.to_dict(file, validation='lax')

                            processor = RecursiveProcessor()
                            processor.cicle_parsing(
                                data=parsed_file[0], past_table_name='', 
                                past_key_name='', uid=str(uuid.uuid4())
                            )

                            psql_cur.execute("BEGIN;")

                            for table in processor.global_list_tables.keys():
                                if not check_table(table, cur=psql_cur):
                                    continue

                                renamed_table = [
                                    {ref_cols.get(k, k): v for k, v in row.items()}
                                    for row in processor.global_list_tables[table]
                                ]
                                rows = [
                                    OrderedDict({col: row.get(col, None) for col in columns_list[table]}) 
                                    for row in renamed_table
                                ]

                                columns = list(rows[0].keys()) if rows else []
                                values = [tuple(d.values()) for d in rows]

                                full_table = f'"{schema_name}"."{table}"'
                                query = sql.SQL("INSERT INTO {} ({cols}) VALUES ({vals});").format(
                                    sql.SQL(full_table),
                                    cols=sql.SQL(', ').join([sql.SQL('"{}"'.format(col)) for col in columns]),
                                    vals=sql.SQL(', ').join([sql.Placeholder()] * len(columns))
                                )
                                psql_cur.executemany(query, values)

                            psql_conn.commit()
                            mark_file(zip_path, file_path, True, connection=psql_conn, cur=psql_cur)

                        except Exception as e:
                            all_files_success = False
                            print(f"\n Ошибка: {e}")
                            print('>parent_path', path)
                            print('>path', zip_path)
                            print('>child_path', file_path)
                            psql_conn.rollback()

                    if all_files_success:
                        mark_zip(zip_path, True, connection=psql_conn, cur=psql_cur)
                        print(' -> Все файлы архива успешно обработаны')

    except Exception as ex:
        print(f"\n{ex}")
    finally:
        ssh_conn.close()
        psql_conn.close()
