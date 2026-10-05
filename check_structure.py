"""
Скрипт для проверки структуры справочников в 1С
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from dotenv import load_dotenv
load_dotenv()

try:
    import win32com.client

    # Подключаемся к 1С
    connector = win32com.client.Dispatch("V83.COMConnector")
    db_path = os.getenv('ONE_C_DB_PATH', 'C:\\1C\\Pshon')
    user = os.getenv('ONE_C_USER', '')
    pwd = os.getenv('ONE_C_PASSWORD', '')
    connection_string = f"File='{db_path}';Usr='{user}';Pwd='{pwd}';"

    print(f"Подключение к: {db_path}")
    connection = connector.Connect(connection_string)
    print("✅ Подключено к 1С\n")

    # Получаем метаданные
    metadata = connection.Metadata

    print("=" * 60)
    print("СПРАВОЧНИКИ В БАЗЕ 1С")
    print("=" * 60)

    # Справочники
    catalogs = metadata.Catalogs
    print(f"\nВсего справочников: {catalogs.Count()}\n")

    for i in range(catalogs.Count()):
        catalog = catalogs.Get(i)
        print(f"{i+1}. {catalog.Name} ({catalog.Synonym})")

    print("\n" + "=" * 60)
    print("ДОКУМЕНТЫ В БАЗЕ 1С")
    print("=" * 60)

    # Документы
    documents = metadata.Documents
    print(f"\nВсего документов: {documents.Count()}\n")

    for i in range(documents.Count()):
        doc = documents.Get(i)
        print(f"{i+1}. {doc.Name} ({doc.Synonym})")

    connection.Close()
    print("\n✅ Проверка завершена")

except Exception as e:
    print(f"❌ Ошибка: {e}")
    import traceback
    traceback.print_exc()
