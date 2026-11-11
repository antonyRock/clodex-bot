"""Тесты MCP Clodex. Запуск: cd clodex-mcp && python -W ignore::ResourceWarning -m unittest test_clodex  (в venv с fastmcp).
Работают на временной копии «базы», настоящих данных не трогают."""
import asyncio
import json
import os
import sys
import tempfile
import unittest
from datetime import date, timedelta

TMP = tempfile.mkdtemp(prefix="clodex-test-")
WORK, CACHE = os.path.join(TMP, "Claude_AI"), os.path.join(TMP, "cache")
os.environ.update(CLODEX_WORKDIR=WORK, CLODEX_CACHE=CACHE, CLODEX_TG_CHAT="111", CLODEX_AGENT_DIR="03_Проекты/04_Агент",
                  CLODEX_BOT_DIR=os.path.join(TMP, "bot"), CLODEX_ALLOWED_LOGIN="owner")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import clodex_access as A  # noqa: E402
import clodex_tools as T  # noqa: E402
from clodex_access import GuardError  # noqa: E402

TODAY = T.now().date()


def put(rel, text):
    p = os.path.join(WORK, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w", encoding="utf-8").write(text)
    return p


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        put("CLAUDE.md", "# Правила\nкорень")
        put("01_Главный контекст/01_Кто.md", "__ИМЯ__, продакт")
        put("01_Главный контекст/05_Задачи/01_Личные.md",
            f"# Личные\n- [ ] Л-001 · {TODAY - timedelta(days=2)} · — · Просроченное дело · __ИМЯ__\n  - дальше: позвонить\n"
            f"- [ ] Л-002 · без срока · — · Без срока · агент\n- [x] Л-003 · {TODAY} · — · Закрыто · __ИМЯ__\n"
            f"- [ ] Л-004 · {TODAY + timedelta(days=30)} · mcp · Далёкое · __ИМЯ__ и агент\n  - вопрос: есть ли Plus\n")
        put("01_Главный контекст/05_Задачи/06_События.md",
            f"# События\n- [ ] Е-001 · {TODAY + timedelta(days=3)} · t6d · Встреча · встреча\n  - время: 11:00\n"
            f"- [ ] Е-002 · {TODAY + timedelta(days=90)} · t6d · Далёкая · веха\n")
        put("03_Проекты/04_Агент/24_MCP Clodex/02_Для приложения.md", "Ты в приложении.")
        put("03_Проекты/04_Агент/CLAUDE.md", "правила проекта")
        put("03_Проекты/04_Агент/файл.md", "строка один\nстрока ТЕЛЕФОН два\n")
        put("04_Системные файлы/06_Доступы/Доступы.md", "пароль=1")
        put("02_Глубокий контекст/10_Документы/скан.md", "паспорт")
        put("06_Загрузки/01_От агента/.keep.md", "x")
        os.makedirs(os.path.join(WORK, ".stversions"), exist_ok=True)
        put(".stversions/старое.md", "секрет")
        os.makedirs(CACHE, exist_ok=True)
        open(os.path.join(CACHE, "переписка.txt"), "w").write("в кэше есть слово МАРКЕР\n")


class TestAccess(Base):
    def test_blocked(self):
        for p in ("04_Системные файлы/06_Доступы/Доступы.md", "02_Глубокий контекст/10_Документы/скан.md",
                  ".stversions/старое.md", "../etc/passwd", "/etc/passwd", "a/../../x"):
            with self.assertRaises(GuardError, msg=p):
                A.resolve(p)

    def test_symlink_out(self):
        os.symlink("/etc", os.path.join(WORK, "06_Загрузки", "линк"))
        with self.assertRaises(GuardError):
            A.resolve("06_Загрузки/линк/passwd")

    def test_cache_read_only(self):
        self.assertTrue(A.resolve(os.path.join(CACHE, "переписка.txt")).endswith("переписка.txt"))
        with self.assertRaises(GuardError):
            A.resolve(os.path.join(CACHE, "новый.txt"), write=True)

    def test_write_rules(self):
        for p in ("CLAUDE.md", "AGENTS.md", "04_Системные файлы/скрипт.py", "новая_папка/файл.md", "новый_в_корне.md",
                  "06_Загрузки/ключ.env", "06_Загрузки/my_token.md"):
            with self.assertRaises(GuardError, msg=p):
                A.resolve(p, write=True)
        A.resolve("06_Загрузки/01_От агента/новый.md", write=True)
        A.resolve("03_Проекты/04_Агент/CLAUDE.md", write=True)   # CLAUDE.md проекта править можно


class TestTools(Base):
    def test_read_data_note_and_rules(self):
        r = T.read("03_Проекты/04_Агент/файл.md")
        self.assertIn("данные из файла", r)
        self.assertIn("CLAUDE.md", r)
        self.assertIn("ТЕЛЕФОН", r)

    def test_read_blocked(self):
        with self.assertRaises(GuardError):
            T.read("04_Системные файлы/06_Доступы/Доступы.md")

    def test_read_paging(self):
        put("06_Загрузки/01_От агента/длинный.md", "ы" * 5000)
        r = T.read("06_Загрузки/01_От агента/длинный.md", 0, 1000)
        self.assertIn("offset=1000", r)

    def test_list_hides_closed(self):
        self.assertNotIn("Доступы", T.list_dir("04_Системные файлы"))
        self.assertNotIn(".stversions", T.list_dir(""))
        self.assertNotIn("10_Документы", T.list_dir("02_Глубокий контекст"))

    def test_search(self):
        r = T.search("телефон")
        self.assertIn("файл.md:2", r)
        self.assertNotIn("пароль", T.search("пароль"))
        self.assertNotIn("паспорт", T.search("паспорт"))
        self.assertIn("МАРКЕР", T.search("маркер"))
        self.assertNotIn("МАРКЕР", T.search("маркер", scope="base"))
        self.assertIn("[имя файла]", T.search("переписка", scope="cache"))

    def test_search_paging(self):
        put("06_Загрузки/01_От агента/много.md", "\n".join("попугай %d" % i for i in range(120)))
        a, b = T.search("попугай", page=0), T.search("попугай", page=1)
        self.assertIn("page=1", a)
        self.assertNotEqual(a, b)

    def test_tasks(self):
        r = T.tasks()
        self.assertIn("Просроченное дело", r)
        self.assertNotIn("Закрыто", r)
        self.assertIn("(просрочено: 1)", r)
        self.assertIn("дальше: позвонить", r)
        self.assertIn("Далёкое", T.tasks(project="mcp"))
        self.assertNotIn("Далёкое", T.tasks(due_before=str(TODAY)))
        w = T.tasks(waiting_for_anton=True)
        self.assertIn("Далёкое", w)
        self.assertNotIn("Без срока", w)
        with self.assertRaises(GuardError):
            T.tasks(sphere="чужие")

    def test_start(self):
        r = T.start()
        for s in ("Ты в приложении.", "корень", "Просроченное дело", "Встреча", "01_Кто.md"):
            self.assertIn(s, r)
        self.assertNotIn("Далёкая", r)

    def test_edit_write_append_backup(self):
        p = "06_Загрузки/01_От агента/правка.md"
        calls = []
        run = lambda: (calls.append(1) or (True, ["ok"]))   # noqa: E731
        T.write(p, "один\nдва\n", run=run)
        with self.assertRaises(GuardError):
            T.write(p, "ещё")                    # без overwrite
        T.edit(p, "два", "три", run=run)
        T.append(p, "четыре", run=run)
        self.assertEqual(open(os.path.join(WORK, p), encoding="utf-8").read(), "один\nтри\nчетыре\n")
        with self.assertRaises(GuardError):
            T.edit(p, "нет такого", "x")
        T.write(p, "один\nодин\n", overwrite=True)
        with self.assertRaises(GuardError):
            T.edit(p, "один", "x")               # не единственный
        self.assertTrue(os.path.isdir(A.BACKUP))
        self.assertEqual(calls, [], "пересборка не нужна для обычных файлов")
        leftovers = [f for f in os.listdir(os.path.dirname(os.path.join(WORK, p))) if f.endswith(".mcp-tmp")]
        self.assertEqual(leftovers, [])

    def test_rebuild_on_task_file(self):
        calls = []
        T.append("01_Главный контекст/05_Задачи/01_Личные.md", "- [ ] Л-099 · без срока · — · Новое · __ИМЯ__",
                 run=lambda: (calls.append(1) or (True, ["✅ Личный пульт пересобирается"])))
        self.assertEqual(calls, [1])
        r = T.edit("01_Главный контекст/05_Задачи/01_Личные.md", "Л-099", "Л-098", run=lambda: (True, ["✅ перестроено"]))
        self.assertIn("перестроено", r)

    def test_move(self):
        a = put("06_Загрузки/01_От агента/перенос.md", "m")
        r = T.move("06_Загрузки/01_От агента/перенос.md", "03_Проекты/04_Агент/")
        self.assertTrue(os.path.exists(os.path.join(WORK, "03_Проекты/04_Агент/перенос.md")))
        self.assertFalse(os.path.exists(a))
        self.assertIn("→", r)
        with self.assertRaises(GuardError):
            T.move("03_Проекты/04_Агент/перенос.md", "03_Проекты/04_Агент/файл.md")   # перезапись
        with self.assertRaises(GuardError):
            T.move("03_Проекты/04_Агент/CLAUDE.md", "06_Загрузки/01_От агента/")      # служебное имя
        with self.assertRaises(GuardError):
            T.move("03_Проекты/04_Агент/файл.md", "/tmp/")                              # наружу

    def test_summary_and_journal(self):
        T.chat_journal("claude", "clodex_read", "Что у меня сегодня?", "файл")
        T.summary("решили X")
        j = open(os.path.join(WORK, T.MCP_DIR, "03_Журнал чатов", f"{T.now():%Y-%m}.md"), encoding="utf-8").read()
        self.assertIn("Что у меня сегодня?", j)
        self.assertIn("решили X", j)

    def test_ext_limit(self):
        T._ext_calls.clear()
        for _ in range(10):
            T.ext_limit()
        with self.assertRaises(GuardError):
            T.ext_limit()
        T._ext_calls.clear()

    def test_email_validation(self):
        T._ext_calls.clear()
        with self.assertRaises(GuardError):
            T.email_draft("не-адрес", "т", "б")
        with self.assertRaises(GuardError):
            T.email_draft("a@b.ru", "тема\nBcc: x@y.ru", "б")


class TestServer(Base):
    """Настоящий MCP-клиент в памяти: схема инструментов, подсказки, отказ чужому."""

    def setUp(self):
        import clodex_server as S
        self.S = S
        self._orig = S.owner_check
        self.mcp = S.build(None)

    def tearDown(self):
        self.S.owner_check = self._orig

    def run_async(self, coro):
        return asyncio.new_event_loop().run_until_complete(coro)

    def test_tools_listed(self):
        from fastmcp import Client

        async def go():
            async with Client(self.mcp) as c:
                return await c.list_tools()
        tools = {t.name: t for t in self.run_async(go())}
        self.assertEqual(len(tools), 16)
        for name in ("clodex_start", "clodex_read", "clodex_search", "clodex_tasks", "clodex_list", "clodex_status", "clodex_reminders"):
            self.assertTrue(tools[name].annotations.readOnlyHint, name)
        for name in ("clodex_edit", "clodex_write", "clodex_append", "clodex_move"):
            self.assertFalse(tools[name].annotations and tools[name].annotations.readOnlyHint, name)
        for t in tools.values():
            self.assertIn("question", t.inputSchema["required"], t.name)
            self.assertLessEqual(len(t.name), 64)

    def test_denied_without_owner(self):
        from fastmcp import Client
        from fastmcp.exceptions import ToolError

        async def go():
            async with Client(self.mcp) as c:
                return await c.call_tool("clodex_start", {"question": "q"})
        with self.assertRaises(ToolError) as e:
            self.run_async(go())
        self.assertIn("Доступ закрыт", str(e.exception))

    def test_call_as_owner(self):
        from fastmcp import Client
        self.S.owner_check = lambda: None

        async def go():
            async with Client(self.mcp) as c:
                a = await c.call_tool("clodex_tasks", {"question": "что висит?"})
                try:
                    await c.call_tool("clodex_read", {"question": "q", "path": "04_Системные файлы/06_Доступы/Доступы.md"})
                    b = "NO ERROR"
                except Exception as e:  # noqa: BLE001
                    b = str(e)
                return a, b
        a, b = self.run_async(go())
        self.assertIn("Просроченное дело", a.content[0].text)
        self.assertIn("закрыты", b)


if __name__ == "__main__":
    unittest.main()
