import paramiko
import psycopg2


def create_conn_psyco():
    "Подключение к серверу с исходниками закупок"
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname='HOSTNAME', 
        username='USERNAME', 
        password='PASSWORD', 
        port=22
    )
    return client


def create_conn_postgre():
    "Подключение к postgre"
    conn = psycopg2.connect(
        host="HOSTNAME",
        database="DB_NAME",
        user="USERNAME",
        password="PASSWORD")
    return conn