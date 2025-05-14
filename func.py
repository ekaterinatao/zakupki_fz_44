import pandas as pd
from collections import OrderedDict
import re
from typing import List, Dict
import numpy as np


def check_float(x) -> bool:
    try:
        float(x)
        return True
    except:
        return False


def detect_column_type(series: pd.Series) -> str:
    """Функция для автоматического определения типа данных при создании БД.
        Текущая версия не учитывает все ошибки: 
        некоторые текстовые поля фактически varchar(2000)
    """
    try:
        unique_values = set(series.dropna().unique())
        if series.dtype == 'bool' or unique_values == {True, False}:
            return "BOOLEAN"
    except:
        pass

    if series.dropna().apply(check_float).all():
        return "CHARACTER VARYING(128)"
    elif series.dtype == 'O' or series.dtype == 'string':
        max_length = series.astype(str).str.len().dropna().max()
        if (max_length > 40 
            or 'name' in series.name.lower() 
            or 'address' in series.name.lower()
            or 'info' in series.name.lower()
            or 'reason' in series.name.lower()): 
            return "TEXT"
        else:
            return "CHARACTER VARYING(128)"

    return "TEXT"


def shorten_columns(columns: List[str], max_len: int=63) -> List[str]:
    "Получение коротких наименований столбцов"
    new = []
    for col in columns:
        if len(col) <= max_len:
            new.append(col)
            continue

        parts = col.split('.')
        prefix, suffix = parts[0], parts[-1]
        middle = ''.join(parts[1:-1])

        words = re.findall(r'[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)', middle)
        abrv = ''.join(w[:3] for w in words)
        new_col = f"{prefix}.{abrv}.{suffix}"

        if len(new_col) > max_len:
            allowed_len = max_len - len(prefix) - len(suffix) - 2
            new_col = f"{prefix}.{abrv[:allowed_len]}.{suffix}"

        new.append(new_col)

    return new


def preproc_data(list_tables: List[Dict], table_name: str, schema_name: str='temp_tao'):
    "Обработка таблиц, полученных при рекурсивном парсинге для последующего создания схемы БД"

    df = pd.DataFrame(list_tables[table_name])

    # переименование признаков (т.к. длинные не записываются в БД)
    df_cols = df.columns.tolist()
    new_df_cols = shorten_columns(df_cols)
    col_to_new_dict = OrderedDict({key: col for key, col in zip(df_cols, new_df_cols)})
    df_mod = df.copy().rename(columns=col_to_new_dict)

    # обработка пропусков
    df_mod = df_mod.replace({np.nan: None})

    # определение типов
    df_types = [detect_column_type(df_mod[col]) for col in new_df_cols]

    return df_mod, df_types


def extract_region_and_year(path: str):
    "Сортировка путей zip-архивов. Парсинг сначала новых."
    match_region = re.search(r'fcs_regions/([^/]+)/contracts', path)
    region = match_region.group(1) if match_region else ''
    match_year = re.search(r'_(\d{4})', path)
    year = int(match_year.group(1)) if match_year else 0

    return (region, -year)