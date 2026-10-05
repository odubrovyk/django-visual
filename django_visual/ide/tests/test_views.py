import subprocess
import sys

import pytest

from ide import views
from ide.open_project import project_settings

PROJECT_URL = "/ide/open_project/sample/"


@pytest.fixture
def blog(make_project):
    return make_project("blog")


def installed(root):
    return list(project_settings("sample", str(root)).INSTALLED_APPS)


# welcome

def test_index_creates_home_and_lists_projects(client, projects_home):
    response = client.get("/")
    assert response.status_code == 200
    assert projects_home.is_dir()
    assert response.context["projects"] == []

    (projects_home / "beta").mkdir()
    (projects_home / "alpha").mkdir()
    (projects_home / "notes.txt").write_text("not a project")
    response = client.get("/ide/welcome/")
    assert response.context["projects"] == ["alpha", "beta"]


# create_project

def test_create_project_get(client, projects_home):
    response = client.get("/ide/create_project/", {"template": "cms"})
    assert response.status_code == 200
    assert response.context["template"] == "cms"
    assert response.context["error"] == ""
    assert "_" in response.context["title"]


def test_create_project_post_redirects(client, projects_home):
    response = client.post("/ide/create_project/", {"template": "blog", "title": "my_blog"})
    assert response.status_code == 302
    assert response["Location"] == "/ide/open_project/my_blog/"
    assert (projects_home / "my_blog" / "manage.py").is_file()


@pytest.mark.parametrize("data, message", [
    ({"template": "blog", "title": "bad-name"}, "not a valid"),
    ({"template": "nope", "title": "fine_name"}, "couldn't handle"),
])
def test_create_project_errors(client, projects_home, data, message):
    response = client.post("/ide/create_project/", data)
    assert response.status_code == 200
    assert message in response.context["error"]
    assert response.context["title"] == data["title"]


def test_create_project_existing_name(client, blog):
    settings_py = blog / "sample" / "settings.py"
    original = settings_py.read_text()
    response = client.post("/ide/create_project/", {"template": "blog", "title": "sample"})
    assert response.status_code == 200
    assert "already exists" in response.context["error"]
    assert settings_py.read_text() == original


# open_project

def test_open_project(client, blog):
    response = client.get(PROJECT_URL)
    assert response.status_code == 200
    assert response.context["project_id"] == "sample"
    assert [m["name"] for m in response.context["project_apps"]["blog"]] == ["BlogPost"]
    content = response.content.decode()
    assert "BlogPost" in content
    assert "[sample]<>->[blog]" in content  # apps loop rendered (.items, not .iteritems)
    assert "var project_apps = [" in content and "'blog', " in content


def test_open_project_shows_apps_error(client, blog):
    (blog / "blog" / "models.py").write_text("def broken(:\n")
    response = client.get(PROJECT_URL)
    assert response.status_code == 200
    assert "Error in INSTALLED_APPS" in response.content.decode()


def test_open_missing_project_404(client, projects_home):
    assert client.get("/ide/open_project/ghost/").status_code == 404


# applications

@pytest.mark.parametrize("endpoint", ["create_application", "add_application", "remove_application"])
def test_application_endpoints_get_help(client, blog, endpoint):
    response = client.get(PROJECT_URL + endpoint + "/")
    assert response.status_code == 200
    assert b"POST 'app_name'" in response.content


def test_create_application(client, blog):
    response = client.post(PROJECT_URL + "create_application/", {"app_name": "shop"})
    assert response.content == b"OK"
    assert (blog / "shop" / "apps.py").is_file()
    assert installed(blog)[-1] == "shop"


@pytest.mark.parametrize("data", [{"app_name": "bad-name"}, {}])
def test_create_application_invalid_name(client, blog, data):
    before = installed(blog)
    response = client.post(PROJECT_URL + "create_application/", data)
    assert response.status_code == 400
    assert installed(blog) == before


def test_add_application_is_idempotent(client, blog):
    assert client.post(PROJECT_URL + "add_application/", {"app_name": "blog"}).content == b"OK"
    assert installed(blog).count("blog") == 1

    assert client.post(PROJECT_URL + "add_application/", {"app_name": "django.contrib.sites"}).content == b"OK"
    assert installed(blog)[-1] == "django.contrib.sites"

    before = installed(blog)
    assert client.post(PROJECT_URL + "add_application/", {}).content == b"OK"
    assert installed(blog) == before


def test_remove_application_is_idempotent(client, blog):
    assert client.post(PROJECT_URL + "remove_application/", {"app_name": "blog"}).content == b"OK"
    assert "blog" not in installed(blog)
    response = client.post(PROJECT_URL + "remove_application/", {"app_name": "blog"})
    assert response.status_code == 200
    assert response.content == b"OK"


def test_add_model_redirects(client, blog):
    response = client.post(PROJECT_URL + "add_model/", {"application": "blog", "new_model_name": "Tag"})
    assert response.status_code == 302
    assert response["Location"] == PROJECT_URL
    assert "class Tag(models.Model):" in (blog / "blog" / "models.py").read_text()


# files

def test_save_and_open_utf8_round_trip(client, tmp_path):
    target = tmp_path / "notes.py"
    text = "# café ✓\nprint('ok')\n"
    response = client.post("/ide/save_file/", {"path": str(target), "content": text})
    assert response.content == b"File saved"
    assert target.read_bytes() == text.encode("utf-8")

    response = client.get("/ide/open_file/", {"path": str(target)})
    assert response.content.decode("utf-8") == text


def test_open_binary_file(client, blog):
    database = blog / "db.sqlite3"
    response = client.get("/ide/open_file/", {"path": str(database)})
    assert response.status_code == 200
    assert response.content == database.read_bytes()


def test_open_file_without_path(client):
    assert client.get("/ide/open_file/").content == b""


def test_save_file_get(client):
    assert b"POST 'path'" in client.get("/ide/save_file/").content


# run / stop

def test_run_project_post_starts_worker(client, blog, monkeypatch):
    calls = []
    monkeypatch.setattr(views, "run_manage", lambda *args: calls.append(args) or 4321)
    response = client.post(PROJECT_URL + "run_project/")
    assert response.content == b"4321"
    assert calls == [("sample", str(blog))]


def test_run_project_get_returns_log(client, blog, settings, tmp_path):
    assert client.get(PROJECT_URL + "run_project/").content == b""
    assert client.get(PROJECT_URL + "run_project/", {"pid": "1"}).content == b""

    (tmp_path / "project_run.log").write_text("Starting…\n", encoding="utf-8")
    assert settings.RUN_LOG_FILE == str(tmp_path / "project_run.log")
    response = client.get(PROJECT_URL + "run_project/", {"pid": "1"})
    assert response.content.decode("utf-8") == "Starting…\n"


def test_stop_project_kills_process(client, blog):
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    response = client.post(PROJECT_URL + "stop_project/", {"pid": str(proc.pid)})
    assert response.content == b"OK"
    assert proc.wait(timeout=5) == -9


def test_stop_project_unknown_pid(client, blog):
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    response = client.post(PROJECT_URL + "stop_project/", {"pid": str(proc.pid)})
    assert b"No such process" in response.content


def test_stop_project_without_pid(client, blog):
    assert client.post(PROJECT_URL + "stop_project/").content == b""


def break_settings(root):
    with open(root / "sample" / "settings.py", "a") as f:
        f.write("\nFOO = (\n")


def test_open_project_with_broken_settings_shows_banner(client, blog):
    break_settings(blog)
    response = client.get(PROJECT_URL)
    assert response.status_code == 200
    assert "Error in INSTALLED_APPS" in response.content.decode()


@pytest.mark.parametrize("endpoint", ["create_application", "add_application", "remove_application"])
def test_application_endpoints_with_broken_settings(client, blog, endpoint):
    break_settings(blog)
    response = client.post(PROJECT_URL + endpoint + "/", {"app_name": "shop"})
    assert response.status_code == 400
    assert b"SyntaxError" in response.content
