from dataclasses import dataclass
import os
from environs import Env


@dataclass
class ParsingConfig:
    xml_schema_path: str   # путь к xsd схеме
    year_start: int        # год, с которого начинается парсинг
    year_end: int          # год, на котором оканчивается парсинг, включительно
    parent_folder: str     # папка для парсинга, на данный момент только 'contracts'
    doc_type: str          # тип документа для парсинга, на данный момент только 'contract'


def load_config(path: str='.env') -> ParsingConfig:

    env: Env = Env()
    env.read_env()

    return ParsingConfig(
        xml_schema_path='scheme_15_1_i1_mod/fcsExport.xsd',
        year_start=2014,
        year_end=2024,
        parent_folder='contracts',
        doc_type='contract'
    )