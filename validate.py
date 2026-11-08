"""Run the overlay against the original test suite without changing project files."""
import importlib.util
from pathlib import Path
import sys
import unittest

PACKAGE = Path(__file__).resolve().parent
ROOT = PACKAGE.parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT))
import src
src.__path__.insert(0, str(PACKAGE / 'files/src'))
spec = importlib.util.spec_from_file_location('app', PACKAGE / 'files/app.py')
app = importlib.util.module_from_spec(spec)
sys.modules['app'] = app
spec.loader.exec_module(app)
app.ROOT = ROOT
suite = unittest.TestLoader().discover(str(ROOT / 'tests'))
suite.addTests(unittest.TestLoader().discover(str(PACKAGE / 'tests'), top_level_dir=str(PACKAGE / 'tests')))
result = unittest.TextTestRunner(verbosity=1).run(suite)
raise SystemExit(not result.wasSuccessful())
