# GitLab Watcher - Részletes Dokumentáció

Ez a dokumentáció a GitLab Watcher projekt teljes körű technikai leírását tartalmazza.

---

## Tartalomjegyzék

1. [Projekt áttekintés és cél](#1-projekt-áttekintés-és-cél)
2. [Részletes architektúra leírás](#2-részletes-architektúra-leírás)
3. [Telepítési útmutató](#3-telepítési-útmutató)
4. [Konfigurációs lehetőségek](#4-konfigurációs-lehetőségek)
5. [Működési folyamatok](#5-működési-folyamatok)
6. [Állapotkezelés részletei](#6-állapotkezelés-részletei)
7. [API integrációk](#7-api-integrációk)
8. [Hibakezelés és recovery mechanizmusok](#8-hibakezelés-és-recovery-mechanizmusok)
9. [Fejlesztői útmutató](#9-fejlesztői-útmutató)
10. [Lehetséges fejlesztési irányok](#10-lehetséges-fejlesztési-irányok)

---

## 1. Projekt áttekintés és cél

### 1.1 Célkitűzés

A **GitLab Watcher** egy Python alapú daemon, amely automatizálja a szoftverfejlesztési munkafolyamatokat GitLab környezetben. A rendszer Claude CLI-t használ a mesterséges intelligencia támogatta kódoláshoz, lehetővé téve az issue-k automatikus feldolgozását és a merge request kommentekre való válaszadást.

### 1.2 Főbb funkciók

| Funkció | Leírás |
|---------|--------|
| **Issue Processing** | A konfigurált felhasználóhoz rendelt issue-k automatikus feldolgozása |
| **MR Comment Processing** | Merge request kommentekre történő automatikus válaszadás |
| **Post-Merge Cleanup** | Merged MR-ek után automatikus takarítás (branch törlés, master frissítés) |
| **Discord Notifications** | Valós idejű értesítések Discord webhookon keresztül |
| **State Persistence** | Állapot perzisztencia a folyamatok követéséhez |

### 1.3 Technológiai stack

- **Python 3.11+**: Alap programozási nyelv
- **Click**: CLI framework
- **requests**: HTTP kliens a GitLab API kommunikációhoz
- **subprocess**: Git műveletek és Claude CLI végrehajtás
- **dataclasses**: Adatstruktúrák definiálása

### 1.4 Követelmények

- Python 3.11 vagy újabb
- Git telepítve és konfigurálva
- GitLab hozzáférés (Personal Access Token)
- Claude CLI vagy Ollama (opcionális Discord webhook)

---

## 2. Részletes architektúra leírás

### 2.1 Modulok áttekintése

```
src/gitlab_watcher/
├── __init__.py          # Csomag inicializáció, verzió definíció
├── __main__.py          # python -m gitlab_watcher belépési pont
├── cli.py               # Click CLI belépési pont
├── watcher.py           # Fő monitoring ciklus
├── processor.py         # Üzleti logika (issue/MR feldolgozás)
├── gitlab_client.py     # GitLab API kliens
├── git_ops.py           # Git műveletek wrapper
├── config.py            # Konfiguráció kezelés
├── state.py             # Állapot perzisztencia
└── discord.py           # Discord webhook értesítések
```

### 2.2 Részletes modul leírások

#### 2.2.1 `cli.py` - Parancssori felület

```python
@click.command()
@click.option("--config", "-c", default=DEFAULT_CONFIG_PATH, help="Path to config file")
@click.option("--verbose", "-v", is_flag=True, help="Verbose output")
def main(config: str, verbose: bool) -> None:
    """GitLab Watcher - Monitor projects and process issues/MRs."""
    watcher = Watcher(config_path=config, verbose=verbose)
    watcher.run()
```

**Funkciók:**
- Egyszerű CLI interfész a Click framework segítségével
- Konfigurációs fájl elérési út testreszabása
- Verbose mód debug célból

#### 2.2.2 `watcher.py` - Központi koordinátor

A `Watcher` osztály a rendszer központi koordinátora, amely:
- Inicializálja az összes függőséget (GitLab kliens, Discord webhook, State manager, Processor)
- Végrehajtja a fő monitoring ciklust
- Kezeli az issue és MR státusz ellenőrzéseket

**Főbb metódusok:**

| Metódus | Felelősség |
|---------|------------|
| `__init__()` | Inicializáció, konfiguráció betöltése, dependency injection |
| `_extract_from_remote()` | GitLab URL és token kinyerése git remote URL-ből |
| `check_issues()` | Új issue-k keresése és feldolgozás |
| `check_mr_status()` | MR státusz ellenőrzése (merge, komment) |
| `run()` | Fő ciklus |

**Dependency Injection:**
A `Watcher` támogatja a dependency injection-t, ami tesztelhetővé teszi:

```python
def __init__(
    self,
    config_path: str = DEFAULT_CONFIG_PATH,
    verbose: bool = False,
    *,
    gitlab: Optional[GitLabClient] = None,
    discord: Optional[DiscordWebhook] = None,
    processor: Optional[Processor] = None,
    state: Optional[StateManager] = None,
) -> None:
```

#### 2.2.3 `processor.py` - Üzleti logika

A `Processor` osztály felelős az issue-k és MR kommentek tényleges feldolgozásáért.

**Főbb metódusok:**

| Metódus | Leírás |
|---------|--------|
| `_run_claude()` | Claude CLI végrehajtása megadott prompttal |
| `process_issue()` | Issue feldolgozása: branch létrehozás, Claude futtatás, MR létrehozás |
| `process_comment()` | MR komment feldolgozása: branch checkout, Claude futtatás, push |
| `cleanup_after_merge()` | Takarítás merge után: master frissítés, branch törlés |

**AI Tool módok:**

A rendszer négy módot támogat az AI tool futtatásához:

| Mód | Parancs | Leírás |
|-----|---------|--------|
| `ollama` | `ollama launch claude -- -p --permission-mode acceptEdits "<prompt>"` | Alapértelmezett mód, Ollama konténeren keresztül |
| `direct` | `claude -p --permission-mode acceptEdits "<prompt>"` | Közvetlen Claude CLI hívás |
| `opencode` | `opencode "<prompt>"` | Opencode CLI használata |
| `custom` | Felhasználó által definiált parancs | Rugalmas, egyedi konfiguráció bármilyen AI eszközhöz |

**Prompt struktúra:**

Issue feldolgozáshoz:
```text
You are working on issue #{issue.iid}: {issue.title}

Issue description:
{issue.description}

Please complete this task. Make the necessary changes and commit them.
Write commit messages in English.
Do not use conventional commit prefixes like feat:, fix:, etc.
Do not add Co-Authored-By signature to commits.
```

MR komment feldolgozáshoz:
```text
You are working on a merge request titled: {mr.title}
Branch: {mr.source_branch}

A reviewer left this feedback:
{comment}

Please address this feedback. Make the necessary changes and commit them.
Write commit messages in English.
Do not use conventional commit prefixes like feat:, fix:, etc.
Do not add Co-Authored-By signature to commits.
```

#### 2.2.4 `gitlab_client.py` - GitLab API kliens

A `GitLabClient` osztály a GitLab REST API v4 interfészt valósítja meg.

**Adatstruktúrák:**

```python
@dataclass
class Issue:
    iid: int
    title: str
    description: str
    web_url: str
    labels: list[str]

@dataclass
class MergeRequest:
    iid: int
    title: str
    web_url: str
    source_branch: str
    state: str

@dataclass
class Note:
    id: int
    body: str
    author_username: str
```

**API metódusok:**

| Metódus | Végpont | Leírás |
|---------|---------|--------|
| `get_issues()` | `GET /projects/:id/issues` | Issue-k listázása |
| `get_merge_requests()` | `GET /projects/:id/merge_requests` | MR-ek listázása |
| `get_merge_request()` | `GET /projects/:id/merge_requests/:iid` | Egy MR lekérése |
| `get_notes()` | `GET /projects/:id/merge_requests/:iid/notes` | Kommentek listázása |
| `update_issue_labels()` | `PUT /projects/:id/issues/:iid` | Issue címkék frissítése |
| `create_merge_request()` | `POST /projects/:id/merge_requests` | MR létrehozása |

**Retry logika:**

A kliens automatikus újrapróbálkozást implementál 5xx hibákra:

```python
def _request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
    """Make HTTP request with retry logic for 5xx errors."""
    last_error: Optional[Exception] = None

    for attempt in range(self.max_retries):
        try:
            response = self.session.request(method, url, **kwargs)
            if response.status_code >= 500:
                last_error = Exception(f"Server error {response.status_code}")
                time.sleep(self.retry_delay * (attempt + 1))
                continue
            return response
        except requests.RequestException as e:
            last_error = e
            time.sleep(self.retry_delay * (attempt + 1))

    raise RuntimeError(f"Request failed after {self.max_retries} retries: {last_error}")
```

#### 2.2.5 `git_ops.py` - Git műveletek

A `GitOps` osztály a Git parancsokat burkolja subprocess hívásokkal.

**Metódusok:**

| Metódus | Git parancs | Leírás |
|---------|-------------|--------|
| `fetch(remote)` | `git fetch <remote>` | Távoli repository frissítése |
| `checkout(branch, create)` | `git checkout [-b] <branch>` | Branch váltás/létrehozás |
| `pull(remote, branch)` | `git pull [<remote> [<branch>]]` | Változások letöltése |
| `push(remote, branch, set_upstream)` | `git push [-u] <remote> <branch>` | Változások feltöltése |
| `delete_branch(branch, force)` | `git branch -D|-d <branch>` | Branch törlése |
| `branch_exists(branch)` | `git rev-parse --verify <branch>` | Branch létezésének ellenőrzése |
| `get_current_branch()` | `git rev-parse --abbrev-ref HEAD` | Aktuális branch neve |
| `get_remote_url(remote)` | `git config --get remote.<remote>.url` | Remote URL lekérése |
| `generate_slug(title)` | - | URL-barát slug generálás (static) |

**Slug generálás:**

A branch nevek automatikus generálásához:

```python
@staticmethod
def generate_slug(title: str, max_length: int = 30) -> str:
    slug = title.lower()
    slug = "".join(c if c.isalnum() else "-" for c in slug)
    while "--" in slug:
        slug = slug.replace("--", "-")
    slug = slug.strip("-")
    return slug[:max_length]
```

Példa: `"Fix bug #123!!!"` → `"fix-bug-123"`

#### 2.2.6 `config.py` - Konfiguráció kezelés

A konfiguráció Bash-stílusú fájlokból töltődik be.

**Adatstruktúrák:**

```python
@dataclass
class ProjectConfig:
    project_id: int
    path: Path
    name: str

@dataclass
class Config:
    gitlab_url: str = ""
    gitlab_token: str = ""
    discord_webhook: str = ""
    label_in_progress: str = "In progress"
    label_review: str = "Review"
    gitlab_username: str = "claude"
    poll_interval: int = 30
    ai_tool_mode: str = "ollama"
    ai_tool_custom_command: str = ""
    project_dirs: list[str] = field(default_factory=list)
    projects: list[ProjectConfig] = field(default_factory=list)
```

**Konfigurációs fájl formátum:**

```bash
# Alapvető beállítások
GITLAB_URL="https://git.example.com"
GITLAB_TOKEN="your-token"
DISCORD_WEBHOOK="https://discord.com/api/webhooks/..."

# Workflow címkék
LABEL_IN_PROGRESS="In progress"
LABEL_REVIEW="Review"

# Felhasználó és időzítés
GITLAB_USERNAME="claude"
POLL_INTERVAL=30

# AI tool mód
AI_TOOL_MODE="ollama"
AI_TOOL_CUSTOM_COMMAND=""

# Projektek
PROJECT_DIRS=(
  "/path/to/project1"
  "/path/to/project2"
)
```

**Projektfelfedezés:**

A rendszer automatikusan felfedezi a projekteket a `PROJECT_DIRS` könyvtárakban lévő `PROJECT.md` fájlok alapján. A fájlban a `Project ID: <szám>` sor határozza meg a GitLab projekt azonosítót.

Támogatott formátumok:
- `Project ID: 31`
- `Project ID: **31**`
- `project_id: 31`

#### 2.2.7 `state.py` - Állapotkezelés

A `StateManager` osztály perzisztálja a projektek állapotát JSON fájlokba.

**Állapot struktúra:**

```python
@dataclass
class ProjectState:
    last_mr_iid: Optional[int] = None      # Utolsó MR IID
    last_mr_state: Optional[str] = None     # Utolsó MR státusz
    last_note_id: int = 0                    # Utolsó feldolgozott komment ID
    last_branch: Optional[str] = None        # Utolsó branch neve
    processing: bool = False                 # Feldolgozás folyamatban-e
```

**Főbb metódusok:**

| Metódus | Leírás |
|---------|--------|
| `load(project_id)` | Állapot betöltése (cache-elt) |
| `init_state(project_id)` | Inicializáció induláskor (processing=False) |
| `save(project_id)` | Állapot mentése fájlba |
| `is_processing(project_id)` | Feldolgozás állapot ellenőrzése |
| `set_processing(project_id, bool)` | Feldolgozás jelző beállítása |
| `update_mr_state(...)` | MR állapot frissítése |
| `reset(project_id)` | Állapot teljes törlése |

**Fájl elhelyezkedés:**

```
/tmp/gitlab-watcher/
├── state_42.json    # 42-es projekt állapota
├── state_31.json    # 31-es projekt állapota
└── ...
```

#### 2.2.8 `discord.py` - Discord értesítések

A `DiscordWebhook` osztály Discord webhook üzeneteket küld.

**Értesítés típusok:**

| Metódus | Emoji | Esemény |
|---------|-------|---------|
| `notify_issue_started()` | 🚀 | Issue feldolgozás kezdete |
| `notify_mr_created()` | ✅ | MR létrehozása |
| `notify_changes_applied()` | ✅ | Komment alapú változtatások |
| `notify_mr_merged()` | ✅ | MR merge |
| `notify_cleanup_complete()` | 🧹 | Takarítás befejezése |
| `notify_error()` | ❌ | Hiba esetén |

---

## 3. Telepítési útmutató

### 3.1 Rendszerkövetelmények

- Python 3.11 vagy újabb
- Git (telepítve és elérhető a PATH-ban)
- Claude CLI vagy Ollama (AI végrehajtáshoz)

### 3.2 Telepítés forráskódból

```bash
# Repository klónozása
git clone https://git.gyengus.hu/gyengus/gitlab-watcher.git
cd gitlab-watcher

# Fejlesztői módban telepítés (ajánlott)
pip install -e ".[dev]"

# Vagy normál telepítés
pip install .
```

### 3.3 Függőségek

A `pyproject.toml` alapján:

**Fő függőségek:**
- `click>=8.0.0` - CLI framework
- `requests>=2.28.0` - HTTP kliens

**Fejlesztői függőségek:**
- `pytest>=7.0.0` - Teszt framework
- `pytest-cov>=4.0.0` - Kód fedettség

### 3.4 Konfiguráció beállítása

1. Hozd létre a konfigurációs könyvtárat és fájlt:

```bash
mkdir -p ~/.config/gitlab-watcher
cp gitlab-watcher.conf ~/.config/gitlab-watcher/config.conf
```

2. Töltsd ki a konfigurációt:

```bash
# GitLab kapcsolat
GITLAB_URL="https://your-gitlab-instance.com"
GITLAB_TOKEN="your-personal-access-token"

# Discord webhook (opcionális)
DISCORD_WEBHOOK="https://discord.com/api/webhooks/..."

# Workflow címkék (testreszabható)
LABEL_IN_PROGRESS="In progress"
LABEL_REVIEW="Review"

# Monitorozott felhasználó
GITLAB_USERNAME="claude"

# Polling intervallum (másodperc)
POLL_INTERVAL=30

# AI tool mód: ollama, direct, opencode, custom
AI_TOOL_MODE="ollama"

# Projektek
PROJECT_DIRS=(
  "/path/to/project1"
  "/path/to/project2"
)
```

3. Minden projekthez hozz létre `PROJECT.md` fájlt:

```markdown
Project ID: 42

# Projekt dokumentáció...

## Build parancsok
...
```

### 3.5 Futtatás

```bash
# Alapértelmezett konfigurációval
gitlab-watcher

# Egyéni konfigurációval
gitlab-watcher -c /path/to/config.conf

# Verbose módban
gitlab-watcher --verbose
```

---

## 4. Konfigurációs lehetőségek

### 4.1 Teljes konfigurációs referencia

| Változó | Típus | Alapértelmezett | Leírás |
|---------|-------|-----------------|--------|
| `GITLAB_URL` | string | - | GitLab szerver URL |
| `GITLAB_TOKEN` | string | - | Personal Access Token |
| `DISCORD_WEBHOOK` | string | "" | Discord webhook URL (opcionális) |
| `LABEL_IN_PROGRESS` | string | "In progress" | "Folyamatban" címke neve |
| `LABEL_REVIEW` | string | "Review" | "Véleményezés" címke neve |
| `GITLAB_USERNAME` | string | "claude" | Monitorozott GitLab felhasználó |
| `POLL_INTERVAL` | int | 30 | Polling intervallum (másodperc) |
| `AI_TOOL_MODE` | string | "ollama" | AI tool mód |
| `AI_TOOL_CUSTOM_COMMAND` | string | "" | Egyéni parancs (custom módhoz) |
| `PROJECT_DIRS` | array | [] | Projekt könyvtárak listája |

### 4.2 GitLab Token beszerzése

1. Jelentkezz be a GitLab-ba
2. Menj a **Settings > Access Tokens**
3. Hozz létre új tokent a következő jogokkal:
   - `api` - Teljes API hozzáférés
   - `write_repository` - Repository írás

### 4.3 GitLab URL és Token automatikus felismerése

Ha a konfigurációban nincs megadva `GITLAB_URL` és `GITLAB_TOKEN`, a rendszer megpróbálja kinyerni a git remote URL-ből:

```
https://token@git.example.com/group/project.git  → URL: https://git.example.com, Token: token
https://user:token@git.example.com/group/project.git  → URL: https://git.example.com, Token: token
```

### 4.4 AI Tool módok

#### Ollama mód (alapértelmezett)

```bash
AI_TOOL_MODE="ollama"
```

Előfeltétel: Ollama telepítése és `claude` modell jelenléte.

#### Direct mód

```bash
AI_TOOL_MODE="direct"
```

Közvetlen Claude CLI hívás. Előfeltétel: `claude` parancs elérhető a PATH-ban.

#### Opencode mód

```bash
AI_TOOL_MODE="opencode"
```

Opencode CLI használata. Előfeltétel: `opencode` parancs elérhető a PATH-ban.

#### Custom mód

```bash
AI_TOOL_MODE="custom"
AI_TOOL_CUSTOM_COMMAND="my-ai-tool --prompt {prompt} --workdir {cwd}"
```

Egyéni parancs definiálása bármilyen AI eszközhöz. Elérhető változók:
- `{prompt}` - A prompt szöveg (kötelező)
- `{cwd}` - A munkakönyvtár elérési útja (opcionális)

**Fontos:** A munkakönyvtár automatikusan beállításra kerül a parancs futtatása előtt.
Csak akkor használd a `{cwd}` változót, ha az AI eszköz explicit könyvtár paramétert igényel.

Példák:
```bash
# A tool az aktuális könyvtárban dolgozik - nincs szükség {cwd}-re
AI_TOOL_MODE="custom"
AI_TOOL_CUSTOM_COMMAND="my-claude --prompt {prompt}"

# A tool explicit könyvtár paramétert igényel
AI_TOOL_MODE="custom"
AI_TOOL_CUSTOM_COMMAND="my-opencode --task {prompt} --workspace {cwd}"

# Bármilyen más AI eszköz - csak prompt szükséges
AI_TOOL_MODE="custom"
AI_TOOL_CUSTOM_COMMAND="cursor-agent --message {prompt}"
```

---

## 5. Működési folyamatok

### 5.1 Fő monitoring ciklus

```
┌─────────────────────────────────────┐
│           Watcher.run()             │
│                                     │
│  ┌─────────────────────────────────┐│
│  │  for each project:              ││
│  │    check_mr_status(project)     ││
│  │    check_issues(project)        ││
│  └─────────────────────────────────┘│
│                │                    │
│                ▼                    │
│         sleep(POLL_INTERVAL)        │
│                                     │
└─────────────────────────────────────┘
```

### 5.2 Issue Processing Flow

```
┌────────────────────────────────────────────────────────────────┐
│                    Issue Processing                            │
├────────────────────────────────────────────────────────────────┤
│                                                                │
│  1. check_issues()                                             │
│     ├── Get issues assigned to GITLAB_USERNAME                 │
│     ├── Filter: no "In progress" AND no "Review" label         │
│     └── First matching issue → process_issue()                 │
│                                                                │
│  2. process_issue()                                             │
│     ├── Set processing flag = True                             │
│     ├── Add "In progress" label to issue                       │
│     ├── Discord: "Starting Issue" notification                 │
│     ├── Git: fetch, checkout master, pull                      │
│     ├── Git: checkout -b {iid}-{slug}                          │
│     ├── Run Claude CLI with issue description                  │
│     ├── Git: push -u origin {branch}                           │
│     ├── GitLab: create_merge_request()                          │
│     ├── GitLab: update_issue_labels(["Review"])                │
│     ├── Discord: "MR Created" notification                     │
│     └── Set processing flag = False                            │
│                                                                │
│  Branch naming: {issue_iid}-{slugified_title}                  │
│  Example: 42-fix-login-bug                                     │
│                                                                │
└────────────────────────────────────────────────────────────────┘
```

### 5.3 MR Comment Processing Flow

```
┌────────────────────────────────────────────────────────────────┐
│                 MR Comment Processing                           │
├────────────────────────────────────────────────────────────────┤
│                                                                │
│  1. check_mr_status()                                          │
│     ├── Get open MRs by GITLAB_USERNAME                        │
│     ├── Get latest note on MR                                  │
│     ├── Update state (mr_iid, mr_state, note_id, branch)       │
│     └── If new note AND not from GITLAB_USERNAME:              │
│         └── process_comment()                                  │
│                                                                │
│  2. process_comment()                                          │
│     ├── Set processing flag = True                             │
│     ├── Discord: "Processing Comment" notification             │
│     ├── Git: fetch, checkout {source_branch}                   │
│     ├── Git: pull origin {source_branch}                       │
│     ├── Run Claude CLI with comment text                       │
│     ├── Git: push origin {source_branch}                       │
│     ├── Discord: "Changes Applied" notification                │
│     └── Set processing flag = False                            │
│                                                                │
└────────────────────────────────────────────────────────────────┘
```

### 5.4 Post-Merge Cleanup Flow

```
┌────────────────────────────────────────────────────────────────┐
│                   Post-Merge Cleanup                            │
├────────────────────────────────────────────────────────────────┤
│                                                                │
│  1. check_mr_status()                                          │
│     ├── If state.last_mr_iid is set:                           │
│     │   └── Get MR by iid                                      │
│     └── If MR state == "merged":                               │
│         └── cleanup_after_merge()                              │
│                                                                │
│  2. cleanup_after_merge()                                       │
│     ├── Discord: "MR Merged" notification                      │
│     ├── Git: checkout master                                   │
│     ├── Git: pull                                               │
│     ├── Git: delete branch -D {branch}                         │
│     ├── Discord: "Cleanup complete" notification               │
│     └── State: reset()                                          │
│                                                                │
└────────────────────────────────────────────────────────────────┘
```

### 5.5 Workflow Lifecycle Diagram

```
    ┌─────────┐         ┌─────────────┐         ┌───────────┐
    │  Issue  │         │ Branch + MR │         │   Merged  │
    │ (new)   │ ──────> │   (open)    │ ──────> │  (closed) │
    └─────────┘         └─────────────┘         └───────────┘
         │                     │                       │
         │                     │                       │
         ▼                     ▼                       ▼
    ┌────────────┐        ┌──────────────┐     ┌──────────────┐
    │   Add      │        │  Process MR  │     │   Cleanup    │
    │ "In        │        │  comments    │     │   branch     │
    │ progress"  │        │  (iterate)   │     │   delete     │
    └────────────┘        └──────────────┘     └──────────────┘
         │                     │                       │
         │                     │                       │
         ▼                     ▼                       ▼
    ┌────────────┐        ┌──────────────┐     ┌──────────────┐
    │   Add      │        │   Update     │     │  Reset       │
    │  "Review"  │        │   MR code    │     │  state       │
    └────────────┘        └──────────────┘     └──────────────┘
```

---

## 6. Állapotkezelés részletei

### 6.1 State File Struktúra

Az állapot JSON fájlokban tárolódik:

```json
{
  "last_mr_iid": 42,
  "last_mr_state": "opened",
  "last_note_id": 12345,
  "last_branch": "31-add-new-feature",
  "processing": false
}
```

### 6.2 State Flow

```
┌─────────────────────────────────────────────────────────────┐
│                      State Transitions                      │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Start → init_state()                                       │
│            ├── Load from file (if exists)                   │
│            ├── Reset processing=False                       │
│            └── Save to file                                 │
│                                                             │
│  Process Issue Start:                                       │
│            set_processing(project_id, True)                 │
│                                                             │
│  Process Issue End:                                         │
│            update_mr_state(iid, "opened", note_id, branch)  │
│            set_processing(project_id, False)                │
│                                                             │
│  Process Comment Start:                                     │
│            set_processing(project_id, True)                 │
│                                                             │
│  Process Comment End:                                       │
│            set_processing(project_id, False)                │
│                                                             │
│  Merge Detected:                                            │
│            cleanup_after_merge()                            │
│            reset(project_id)                                │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 6.3 Crash Recovery

A `processing` flag crash recovery-t tesz lehetővé:

- **Induláskor**: `init_state()` minden projektre → `processing=False`
- **Feldolgozás alatt**: `processing=True` → megakadályozza az újrainduló folyamatokat
- **Befejezéskor**: `processing=False`

Ez biztosítja, hogy egy összeomlás után ne induljon el újra egy részlegesen feldolgozott issue.

---

## 7. API integrációk

### 7.1 GitLab API v4

A rendszer a GitLab REST API v4-et használja.

**Használt végpontok:**

| Végpont | Metódus | Használat |
|---------|---------|-----------|
| `/projects/:id/issues` | GET | Issue-k listázása |
| `/projects/:id/issues/:iid` | PUT | Issue címkék frissítése |
| `/projects/:id/merge_requests` | GET, POST | MR-ek listázása, létrehozása |
| `/projects/:id/merge_requests/:iid` | GET | Egy MR lekérése |
| `/projects/:id/merge_requests/:iid/notes` | GET | Kommentek listázása |

**Query paraméterek:**

```python
# Issue-k lekérése
get_issues(
    project_id=42,
    state="opened",
    assignee_username="claude"
)

# MR-ek lekérése
get_merge_requests(
    project_id=42,
    state="opened",
    author_username="claude"
)

# Kommentek lekérése
get_notes(
    project_id=42,
    mr_iid=1,
    sort="desc"  # Csökkenő sorrend (legújabb elől)
)
```

### 7.2 AI Tool CLI Integration

Az AI tool hívás a `Processor._run_claude()` metódusban történik:

```python
def _run_claude(self, prompt: str, repo_path: Path) -> tuple[bool, str]:
    # Parancs összeállítása mód szerint
    if self.ai_tool_mode == "ollama":
        cmd = ["ollama", "launch", "claude", "--", "-p", "--permission-mode", "acceptEdits", prompt]
    elif self.ai_tool_mode == "direct":
        cmd = ["claude", "-p", "--permission-mode", "acceptEdits", prompt]
    elif self.ai_tool_mode == "custom":
        # Placeholder-ek helyettesítése
        cmd = [part.replace("{prompt}", prompt).replace("{cwd}", str(repo_path))
               for part in shlex.split(self.ai_tool_custom_command)]
    elif self.ai_tool_mode == "opencode":
        cmd = ["opencode", prompt]
    elif self.ai_tool_mode == "opencode-custom":
        cmd = [part.replace("{prompt}", prompt).replace("{cwd}", str(repo_path))
               for part in shlex.split(self.ai_tool_custom_command)]

    env = {"CLAUDECODE": ""}  # Environment változó beállítás

    result = subprocess.run(
        cmd,
        cwd=repo_path,
        capture_output=True,
        text=True,
        env=env,
        timeout=600,  # 10 perces timeout
    )
    return result.returncode == 0, result.stdout + result.stderr
```

**Fontos paraméterek:**
- `--permission-mode acceptEdits`: Automatikus szerkesztési engedély
- `-p`: Non-interactive mód
- `CLAUDECODE=""`: Környezeti változó konfliktus elkerülése
- 600 másodperces timeout: Hosszabb futású műveletekhez

### 7.3 Discord Webhook

A Discord webhook egyszerű JSON POST kéréseket használ:

```python
response = requests.post(
    webhook_url,
    json={"content": message},
    headers={"Content-Type": "application/json"},
    timeout=10,
)
# Sikeres válasz: HTTP 204 (No Content)
```

---

## 8. Hibakezelés és recovery mechanizmusok

### 8.1 GitLab API hibák

A `GitLabClient` retry logikát implementál:

```python
# Alapértelmezett: 3 újrapróbálkozés, 1 másodperces késleltetés
client = GitLabClient(
    url="https://git.example.com",
    token="token",
    max_retries=3,
    retry_delay=1.0,
)

# Retry feltételek:
# - 5xx szerver hibák
# - Network hibák (ConnectionError, Timeout)
# NEM retry: 4xx kliens hibák
```

### 8.2 Git műveletek hibái

A GitOps metódusok boolean visszatérési értéket használnak:

```python
# Sikeres művelet
if git.checkout(branch, create=True):
    # folytatás
else:
    # hibakezelés
```

A hibák logolásra kerülnek és a folyamat biztonságosan megszakad.

### 8.3 Claude CLI hibák

```python
try:
    result = subprocess.run(cmd, cwd=repo_path, timeout=600, ...)
    return result.returncode == 0, result.stdout + result.stderr
except subprocess.TimeoutExpired:
    return False, "Claude timed out"
except FileNotFoundError:
    return False, "Claude CLI not found"
```

**Hibák típusai:**
- Timeout (600s után)
- CLI nem található
- Nem nulla exit kód

### 8.4 State recovery

Az állapotkezelés biztosítja a konzisztenciát:

```python
# Induláskor minden projekt processing flag reset
for project in config.projects:
    state.init_state(project.project_id)  # processing = False

# Feldolgozás alatt
state.set_processing(project_id, True)   # Lock
# ... munka ...
state.set_processing(project_id, False)  # Unlock

# Crash esetén a következő indulásnál az init_state() reseteli a flaget
```

### 8.5 Fő ciklus hibakezelés

```python
while True:
    try:
        for project in self.config.projects:
            self.check_mr_status(project)
            self.check_issues(project)
        time.sleep(self.config.poll_interval)
    except KeyboardInterrupt:
        print("\nShutting down...")
        break
    except Exception as e:
        self.logger.error(f"Error in main loop: {e}")
        time.sleep(self.config.poll_interval)  # Folytatás alvás után
```

---

## 9. Fejlesztői útmutató

### 9.1 Projekt struktúra

```
gitlab-watcher/
├── src/gitlab_watcher/      # Forráskód
│   ├── __init__.py
│   ├── __main__.py
│   ├── cli.py
│   ├── watcher.py
│   ├── processor.py
│   ├── gitlab_client.py
│   ├── git_ops.py
│   ├── config.py
│   ├── state.py
│   └── discord.py
├── tests/                    # Tesztek
│   ├── test_watcher.py
│   ├── test_processor.py
│   ├── test_gitlab_client.py
│   ├── test_git_ops.py
│   ├── test_config.py
│   ├── test_discord.py
│   └── test_config_extra.py
├── docs/                     # Dokumentáció
│   └── plans/
├── pyproject.toml            # Projekt konfiguráció
├── README.md                 # Gyors kezdés
└── CLAUDE.md                 # Project ID és fejlesztői megjegyzések
```

### 9.2 Tesztelés

**Teszt futtatása:**

```bash
# Összes teszt
pytest

# Verbose kimenet
pytest -v

# Coverage jelentés
pytest --cov=gitlab_watcher --cov-report=term-missing

# Egy teszt fájl
pytest tests/test_watcher.py

# Egy teszt
pytest tests/test_watcher.py::TestWatcherCheckIssues::test_check_issues_with_backlog_issue
```

**Teszt struktúra:**

A tesztek pytest fixture-eket használnak:

```python
@pytest.fixture
def gitlab_client() -> GitLabClient:
    return GitLabClient(url="https://git.example.com", token="test-token")

@pytest.fixture
def state_manager(tmp_path: Path) -> StateManager:
    return StateManager(tmp_path / "work")

@pytest.fixture
def processor(gitlab_client, discord_webhook, state_manager) -> Processor:
    return Processor(
        gitlab=gitlab_client,
        discord=discord_webhook,
        state=state_manager,
        gitlab_username="claude",
        label_in_progress="In progress",
        label_review="Review",
    )
```

**Mock-olt tesztek:**

A külső függőségek (GitLab API, Git, Claude CLI) mock-olva vannak:

```python
@patch("subprocess.run")
def test_run_claude_success(mock_run, processor, project_config):
    mock_run.return_value = Mock(returncode=0, stdout="Done", stderr="")
    success, output = processor._run_claude("Fix the bug", project_config.path)
    assert success is True
```

### 9.3 Kód minőség

**Type hints:**

A projekt teljes körű type annotation-t használ:

```python
def process_issue(
    self,
    project: ProjectConfig,
    issue: Issue,
) -> bool:
    ...
```

**Docstrings:**

Minden publikus metódus rendelkezik docstring-gel:

```python
def _run_claude(self, prompt: str, repo_path: Path) -> tuple[bool, str]:
    """Run Claude CLI with a prompt based on configured mode.

    Args:
        prompt: The prompt for Claude
        repo_path: Path to the repository

    Returns:
        Tuple of (success, output)
    """
```

### 9.4 Dependency Injection

A `Watcher` osztály támogatja a dependency injection-t a tesztelhetőség érdekében:

```python
# Normál használat
watcher = Watcher(config_path="config.conf")

# Teszteléshez mock-okkal
mock_gitlab = MagicMock(spec=GitLabClient)
mock_discord = MagicMock(spec=DiscordWebhook)
mock_processor = MagicMock(spec=Processor)
state_manager = StateManager(temp_dir)

watcher = Watcher(
    config_path="config.conf",
    gitlab=mock_gitlab,
    discord=mock_discord,
    processor=mock_processor,
    state=state_manager,
)
```

### 9.5 Fejlesztői környezet beállítása

```bash
# Repository klónozása
git clone https://git.gyengus.hu/gyengus/gitlab-watcher.git
cd gitlab-watcher

# Virtuális környezet létrehozása
python -m venv venv
source venv/bin/activate

# Fejlesztői függőségek telepítése
pip install -e ".[dev]"

# Tesztek futtatása
pytest
```

---

## 10. Lehetséges fejlesztési irányok

### 10.1 Rövid távú fejlesztések

| Funkció | Leírás | Prioritás |
|---------|--------|-----------|
| **Logging javítás** | Strukturáltabb logging, naplófájlok rotáció | Magas |
| **Hiba értesítések** | Részletesebb hibaüzenetek Discord-on | Magas |
| **Konfiguráció validáció** | Konfigurációs hibák korai detektálása | Közepes |
| **Retry policy** | Testreszabható újrapróbálkozási stratégia | Közepes |

### 10.2 Közepes távú fejlesztések

| Funkció | Leírás |
|---------|--------|
| **Több GitLab példány** | Több GitLab szerver monitorozása |
| **Adatbázis alapú state** | JSON helyett SQLite/PostgreSQL |
| **Web UI** | Egyszerű web interfész a monitorozáshoz |
| **API endpoint** | REST API az állapot lekérdezéséhez |
| **Metrics export** | Prometheus/Grafana kompatibilis metrikák |

### 10.3 Hosszú távú fejlesztések

| Funkció | Leírás |
|---------|--------|
| **Plugin rendszer** | Testreszabható feldolgozó modulok |
| **Multi-language support** | Több AI modell támogatása (GPT, Gemini, stb.) |
| **Kubernetes deployment** | Containerizált deployment |
| **GitLab Webhook integration** | Valós idejű események webhook-on keresztül |

### 10.4 Ismert korlátok

1. **Lineáris feldolgozás**: Egyszerre csak egy issue/MR folyamat fut projektekenként
2. **Nincs prioritás**: Issue-k feldolgozási sorrendje nincs befolyásolva
3. **Nincs rate limiting**: GitLab API hívások nincs korlátozva
4. **Single-thread**: Nincs párhuzamos feldolgozás

### 10.5 Javasolt refactor-ok

```python
# Jelenleg: Watcher közvetlenül hívja a GitLab API-t
# Javasolt: Service layer bevezetése

class IssueService:
    def get_backlog_issues(self, project_id: int) -> list[Issue]:
        ...

class MergeRequestService:
    def get_open_mrs(self, project_id: int) -> list[MergeRequest]:
        ...

# Előnyök:
# - Jobb tesztelhetőség
# - Könnyebb mock-olás
# -清晰abb felelősségi körök
```

---

## Függelék

### A. Példa konfigurációs fájl

```bash
# ~/.claude/config/gitlab_watcher.conf

# GitLab kapcsolat
GITLAB_URL="https://git.example.com"
GITLAB_TOKEN="glpat-xxxxxxxxxxxx"

# Discord értesítések (opcionális)
DISCORD_WEBHOOK="https://discord.com/api/webhooks/123456/abcdef"

# Workflow címkék
LABEL_IN_PROGRESS="In progress"
LABEL_REVIEW="Review"

# Monitorozott felhasználó
GITLAB_USERNAME="claude"

# Polling intervallum (másodperc)
POLL_INTERVAL=30

# AI tool mód: ollama, direct, custom, opencode, opencode-custom
AI_TOOL_MODE="ollama"

# Egyéni parancs (custom vagy opencode-custom módhoz)
AI_TOOL_CUSTOM_COMMAND=""

# Projektek
PROJECT_DIRS=(
  "/home/user/projects/my-project"
  "/home/user/projects/another-project"
)
```

### B. Példa PROJECT.md fájl

```markdown
# My Project

Project ID: 42

## Build

```bash
make build
```

## Test

```bash
make test
```

## Architecture

This project uses...
```

### C. Environment változók

| Változó | Leírás |
|---------|--------|
| `CLAUDECODE` | Claude CLI kompatibilitáshoz (üresre állítva) |

---

## Kapcsolat

- **Repository**: https://git.gyengus.hu/gyengus/gitlab-watcher
- **Issues**: https://git.gyengus.hu/gyengus/gitlab-watcher/issues
- **Szerző**: Gyengus

---

*Dokumentáció verzió: 1.0.0*
*Utolsó frissítés: 2026-03-11*