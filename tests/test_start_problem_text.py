import errno
import unittest
from pathlib import Path
from collector.gui import start_problem_text

class StartProblemTextTests(unittest.TestCase):
    root = Path('C:/Users/x/Documents/EVE/logs/Gamelogs')

    def test_missing_folder_names_path(self):
        text = start_problem_text(NotADirectoryError(20, 'Gamelogs directory is missing'), self.root)
        self.assertIn('не найдена папка журналов', text)
        self.assertIn(str(self.root), text)
        self.assertIn('не найдена', start_problem_text(FileNotFoundError(errno.ENOENT, 'x'), self.root))

    def test_permission_and_generic(self):
        self.assertIn('нет доступа', start_problem_text(PermissionError(13, 'denied'), self.root))
        self.assertIn('отчёт для поддержки', start_problem_text(ValueError('bad'), self.root))
