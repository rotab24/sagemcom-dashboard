import os
import shutil
import sys
from pathlib import Path

# Configurar antes de importar la app: modo simulado y BD temporal
_tmp = Path(__file__).parent / ".tmp"
_tmp.mkdir(exist_ok=True)
os.environ["SAGEMCOM_MOCK"] = "1"
os.environ["DB_PATH"] = str(_tmp / "test.sqlite3")
# Copia del ejemplo: los tests del editor de automatizaciones la sobrescriben
shutil.copy(Path(__file__).parent.parent / "automations.example.json", _tmp / "automations.json")
os.environ["AUTOMATIONS_FILE"] = str(_tmp / "automations.json")
os.environ["COLLECT_INTERVAL"] = "3600"
# Nunca leer ni escribir el .env real del proyecto desde los tests
os.environ["ENV_FILE"] = str(_tmp / "test.env")
sys.path.insert(0, str(Path(__file__).parent.parent))

db = _tmp / "test.sqlite3"
if db.exists():
    db.unlink()
