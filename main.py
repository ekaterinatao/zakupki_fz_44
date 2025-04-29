from fz44_regions import load_xml_to_bd, get_region_contract_path
import xmlschema

from config import ParsingConfig, load_config


config: ParsingConfig = load_config()


print('... load xml schema')
schema = xmlschema.XMLSchema(config.xml_schema_path)

# получаем отсортированный список путей для парсинга (от 2024 до старых)
regions_paths = get_region_contract_path(
    year_start=config.year_start, 
    year_end=config.year_end, 
    parent_folder=config.parent_folder, 
    doc_type=config.doc_type)


if __name__ == '__main__':
    load_xml_to_bd(regions_paths, schema)