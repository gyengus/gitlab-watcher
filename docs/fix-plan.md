# GitLab Watcher - Javítási Terv

**Készült:** 2026-03-11
**Alapul szolgáló dokumentum:** code-review-report.md
**Összes probléma:** 15 (Critical: 1, High: 2, Medium: 5, Low: 7)

---

## 1. Áttekintés

Ez a dokumentum a GitLab Watcher kód review során azonosított problémák javítási tervét tartalmazza. A javításokat logikus fázisokra bontottam, figyelembe véve a prioritást, a függőségeket és a kockázatokat.

---

## 2. Fázisok összefoglalása

| Fázis | Név | Prioritás | Problémák száma | Becsült idő |
|-------|-----|-----------|-----------------|-------------|
| 1 | Kritikus biztonsági javítások | Critical/High | 3 | 4-6 óra |
| 2 | Teljesítmény-optimalizálás | Medium | 4 | 3-4 óra |
| 3 | Architekturális javítások | Medium | 1 | 2-3 óra |
| 4 | Kódminőség javítások | Low | 7 | 3-4 óra |

---

## 3. Részletes Fázisok

---

## 3.1. Fázis 1: Kritikus biztonsági javítások

**Prioritás:** Critical/High
**Kockázat:** Magas - A biztonsági javítások alapos tesztelést igényelnek
**Függőségek:** Nincs

### 3.1.1. Command Injection Vulnerability (CRITICAL)

**Hely:** `processor.py:49-88` - `_run_claude()` metódus

**Probléma leírása:**
A `_run_claude()` metódus a prompt argumentumot közvetve használja shell parancs összeállítására. A prompt tartalmazza az issue címét és leírást, amelyek felhasználói bemenetek. Ha az `shlex.split()` nem működik helyesen, vagy a custom command template sebezhető, akkor ez command injection-hez vezethet.

**Javítási lépések:**

1. **Prompt validálás és sanitizálás:**
   ```python
   # Új konstansok a processor.py elejére
   MAX_PROMPT_LENGTH = 10000
   FORBIDDEN_PATTERNS = [
       r'\$\([^)]+\)',   # Command substitution $(...)
       r'`[^`]+`',        # Backtick command `...`
       r'\|\s*\w+',       # Pipe to command | cmd
       r';\s*\w+',        # Command chaining ; cmd
       r'&&\s*\w+',       # AND chaining && cmd
       r'\|\|\s*\w+',     # OR chaining || cmd
       r'\$\{[^}]+\}',    # Variable expansion ${...}
       r'\$\w+',          # Variable reference $var
   ]
   ```

2. **Új `_sanitize_prompt()` metódus:**
   ```python
   import re

   def _sanitize_prompt(self, prompt: str) -> str:
       """Sanitize prompt to prevent command injection.

       Args:
           prompt: The raw prompt string

       Returns:
           Sanitized prompt string

       Raises:
           ValueError: If prompt contains forbidden patterns
       """
       if len(prompt) > MAX_PROMPT_LENGTH:
           prompt = prompt[:MAX_PROMPT_LENGTH]

       for pattern in FORBIDDEN_PATTERNS:
           if re.search(pattern, prompt):
               raise ValueError(f"Prompt contains forbidden pattern: {pattern}")

       return prompt
   ```

3. **A `_run_claude()` metódus módosítása:**
   - A prompt sanitizálása a parancs összeállítása előtt
   - A custom command validálása (csak engedélyezett placeholder-ek)
   - A `subprocess.run()` hívásnál a `shell=False` biztosítása (már igaz ez)

4. **Tesztelés:**
   - Egységtesztek a `_sanitize_prompt()` metódushoz
   - Integrációs tesztek különböző rosszindulatú bemenetekkel
   - Fuzzing tesztek a prompt bemenethez

**Érintett fájlok:**
- `src/gitlab_watcher/processor.py`
- `tests/test_processor.py`

**Kockázatok:**
- A túl szigorú szűrés törheti a legitimate use case-eket
- A valid issue leírások tartalmazhatnak code snippet-eket, amik tévesen triggerelik a szűrést

**Enyhítés:**
- A tesztekben kiterjedt legitimate use case-eket kell lefedni
- A hibaüzeneteknek tisztázniuk kell, miért utasították el a bemenetet

---

### 3.1.2. Sensitive Token Exposure in Logs (HIGH)

**Hely:** `watcher.py:84-119` - `_extract_from_remote()` metódus és `watcher.py:240` - hibanaplózás

**Probléma leírása:**
A GitLab token kinyerése a remote URL-ből és a következő sorokban történő használata naplózást eredményezhet, amelyek a tokent tartalmazhatják. A token a `GitLabClient` konstruktorában tárolódik és továbbítható logba hiba esetén.

**Javítási lépések:**

1. **Új `SensitiveDataFilter` osztály létrehozása:**
   ```python
   # Új fájl: src/gitlab_watcher/logging_utils.py
   import logging
   import re

   class SensitiveDataFilter(logging.Filter):
       """Filter to mask sensitive data in log messages."""

       SENSITIVE_PATTERNS = [
           # GitLab tokens (typically 20+ alphanumeric characters)
           (r'([a-zA-Z0-9_-]{20,})', '***TOKEN***'),
           # URLs with authentication
           (r'https://[^:]+:[^@]+@', 'https://***:***@'),
           # URLs with token only
           (r'https://[^@]+@', 'https://***@'),
       ]

       def filter(self, record: logging.LogRecord) -> bool:
           """Filter sensitive data from log record."""
           msg = str(record.msg)
           for pattern, replacement in self.SENSITIVE_PATTERNS:
               msg = re.sub(pattern, replacement, msg)
           record.msg = msg

           # Also filter args if present
           if record.args:
               record.args = tuple(
                   re.sub(pattern, replacement, str(arg)) if isinstance(arg, str) else arg
                   for arg in record.args
                   for pattern, replacement in self.SENSITIVE_PATTERNS
               )

           return True
   ```

2. **A `Watcher` osztály módosítása:**
   - A filter alkalmazása a logger-re az inicializáció során
   - A token maszkolása a debug kimenetekben

3. **A `GitLabClient` osztály módosítása:**
   - A token ne szerepeljen a `__repr__` kimenetben
   - A debug naplózásból legyen kihagyva a token

4. **Tesztelés:**
   - Egységtesztek a `SensitiveDataFilter` osztályhoz
   - Integrációs tesztek a naplózás ellenőrzésére

**Érintett fájlok:**
- `src/gitlab_watcher/logging_utils.py` (új)
- `src/gitlab_watcher/watcher.py`
- `src/gitlab_watcher/gitlab_client.py`
- `tests/test_logging_utils.py` (új)

**Kockázatok:**
- A túl agresszív maszkolás elrejtheti a hibakereséshez szükséges információkat
- A teljesítményre gyakorolt hatás a regex minták miatt

**Enyhítés:**
- A filter csak a production környezetben legyen aktív, vagy konfigurálható legyen
- A minták pontosítása a valós GitLab token formátumokra

---

### 3.1.3. Missing Input Validation on Issue Content (HIGH)

**Hely:** `processor.py:89-189` - `process_issue()` metódus

**Probléma leírása:**
Az issue címe és leírása validálás nélkül kerül átadásra a Claude CLI-nek és használatra a branchnév generálásában. Nincs ellenőrzés a maximális hosszra vagy a veszélyes karakterekre.

**Javítási lépések:**

1. **Új konstansok és validációs függvények:**
   ```python
   # Konstansok
   MAX_TITLE_LENGTH = 255
   MAX_DESCRIPTION_LENGTH = 50000
   MAX_SLUG_LENGTH = 50
   MAX_BRANCH_LENGTH = 100

   # Új metódusok a Processor osztályban
   def _validate_issue_title(self, title: str) -> str:
       """Validate and sanitize issue title.

       Args:
           title: The raw issue title

       Returns:
           Validated and sanitized title
       """
       if not title or not title.strip():
           raise ValueError("Issue title cannot be empty")

       # Truncate to max length
       title = title[:MAX_TITLE_LENGTH]

       # Remove control characters
       title = ''.join(c for c in title if c.isprintable())

       return title.strip()

   def _validate_branch_name(self, branch: str) -> str:
       """Validate branch name is safe.

       Args:
           branch: The proposed branch name

       Returns:
           Validated branch name
       """
       # Git branch name restrictions
       # Cannot start with dot, contain .., or special characters
       branch = branch.strip()

       if not branch:
           return "auto-branch"

       # Remove problematic characters
       branch = re.sub(r'[^\w\-/.]', '-', branch)

       # Remove consecutive hyphens
       while '--' in branch:
           branch = branch.replace('--', '-')

       # Remove leading/trailing hyphens and dots
       branch = branch.strip('-.')

       # Truncate to max length
       if len(branch) > MAX_BRANCH_LENGTH:
           branch = branch[:MAX_BRANCH_LENGTH]

       return branch or "auto-branch"
   ```

2. **A `GitOps.generate_slug()` metódus bővítése:**
   - A `max_length` paraméter kezelése már megvan
   - A speciális karakterek kezelése javítása

3. **A `process_issue()` metódus módosítása:**
   ```python
   def process_issue(self, project: ProjectConfig, issue: Issue) -> bool:
       # Validate issue title
       try:
           validated_title = self._validate_issue_title(issue.title)
       except ValueError as e:
           self.logger.error(f"Invalid issue title: {e}")
           return False

       # Generate and validate branch name
       slug = GitOps.generate_slug(validated_title, max_length=MAX_SLUG_LENGTH)
       branch = self._validate_branch_name(f"{issue.iid}-{slug}")
       # ... folytatás
   ```

4. **Tesztelés:**
   - Egységtesztek a validációs függvényekhez
   - Edge case tesztek: üres cím, túl hosszú cím, speciális karakterek
   - Integrációs tesztek a teljes folyamat ellenőrzésére

**Érintett fájlok:**
- `src/gitlab_watcher/processor.py`
- `src/gitlab_watcher/git_ops.py`
- `tests/test_processor.py`
- `tests/test_git_ops.py`

**Kockázatok:**
- A túl szigorú validáció elutasíthatja legitimate issue-kat
- A branch név generálás változása meglévő branch-okkal inkompatibilissá teheti a rendszert

**Enyhítés:**
- A validációs hibák naplózása és a felhasználó értesítése (Discord webhook)
- A fallback megoldások biztosítása (pl. "auto-branch" név)

---

### 3.1.4. Fázis 1 Tesztelési Stratégia

**Egységtesztek:**
1. `test_sanitize_prompt()` - különböző rosszindulatú bemenetek tesztelése
2. `test_validate_issue_title()` - cím validálás tesztelése
3. `test_validate_branch_name()` - branch név validálás tesztelése
4. `test_sensitive_data_filter()` - token maszkolás tesztelése

**Integrációs tesztek:**
1. Teljes issue feldolgozás tesztelése rosszindulatú bemenettel
2. Naplózás ellenőrzése, hogy nincs-e benne token
3. Branch létrehozás tesztelése különböző címekkel

**Biztonsági tesztek:**
1. Command injection próbálkozások tesztelése
2. Token exposure tesztek a naplókban
3. Fuzzing tesztek a bemeneti validációhoz

---

## 3.2. Fázis 2: Teljesítmény-optimalizálás

**Prioritás:** Medium
**Kockázat:** Közepes - A teljesítmény javítások befolyásolhatják a meglévő működést
**Függőségek:** Fázis 1 befejezése ajánlott (de nem kötelező)

### 3.2.1. Missing Request Timeout + Missing Connection Pooling (EGYÜTT MEGOLDVA)

**Hely:** `gitlab_client.py:45-65` és `gitlab_client.py:77`

**Probléma leírása:**
A `_request()` metódus nem ad meg timeout-ot a HTTP kéréshez, és a `requests.Session` nincs konfigurálva connection pooling-gal.

**Javítási lépések:**

Ez a két probléma együtt megoldható a `GitLabClient` osztály átalakításával:

1. **Új importok és konstansok:**
   ```python
   from requests.adapters import HTTPAdapter
   from urllib3.util.retry import Retry

   DEFAULT_TIMEOUT = 30.0
   DEFAULT_MAX_RETRIES = 3
   DEFAULT_RETRY_DELAY = 1.0
   DEFAULT_POOL_CONNECTIONS = 10
   DEFAULT_POOL_MAXSIZE = 20
   ```

2. **A `GitLabClient.__init__()` módosítása:**
   ```python
   def __init__(
       self,
       url: str,
       token: str,
       max_retries: int = DEFAULT_MAX_RETRIES,
       retry_delay: float = DEFAULT_RETRY_DELAY,
       timeout: float = DEFAULT_TIMEOUT,
       pool_connections: int = DEFAULT_POOL_CONNECTIONS,
       pool_maxsize: int = DEFAULT_POOL_MAXSIZE,
   ) -> None:
       """Initialize GitLab client with connection pooling and timeout.

       Args:
           url: GitLab instance URL
           token: Personal access token
           max_retries: Maximum retries on 5xx errors
           retry_delay: Delay between retries
           timeout: Request timeout in seconds
           pool_connections: Connection pool size
           pool_maxsize: Maximum connections in pool
       """
       self.base_url = url.rstrip("/")
       self._token = token  # Private to avoid accidental logging
       self.max_retries = max_retries
       self.retry_delay = retry_delay
       self.timeout = timeout

       # Configure session with connection pooling
       self.session = requests.Session()
       self.session.headers.update({"PRIVATE-TOKEN": token})

       # Configure retry strategy
       retry_strategy = Retry(
           total=max_retries,
           backoff_factor=retry_delay,
           status_forcelist=[429, 500, 502, 503, 504],
       )

       # Configure adapter with connection pooling
       adapter = HTTPAdapter(
           max_retries=retry_strategy,
           pool_connections=pool_connections,
           pool_maxsize=pool_maxsize,
       )

       self.session.mount("https://", adapter)
       self.session.mount("http://", adapter)
   ```

3. **A `_request()` metódus módosítása:**
   ```python
   def _request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
       """Make HTTP request with timeout and retry logic."""
       # Set default timeout if not provided
       kwargs.setdefault("timeout", self.timeout)

       # ... meglévő retry logika ...
   ```

4. **Tesztelés:**
   - Egységtesztek a timeout kezeléshez
   - Integrációs tesztek a connection pooling-gal
   - Terheléses tesztek

**Érintett fájlok:**
- `src/gitlab_watcher/gitlab_client.py`
- `tests/test_gitlab_client.py`

**Kockázatok:**
- A connection pooling változhat a viselkedés hosszú futású folyamatoknál
- A timeout túl rövid időtúllépést okozhat lassú hálózatokon

---

### 3.2.2. Repeated GitLab API Calls Without Caching

**Hely:** `watcher.py:154-212` - `check_mr_status()` metódus

**Probléma leírása:**
A `check_mr_status()` metódus minden poll ciklusban meghívja a `get_merge_requests()`, `get_merge_request()`, és `get_notes()` API-kat. Nincs semmilyen caching.

**Javítási lépések:**

1. **Cache osztály létrehozása:**
   ```python
   # Új fájl: src/gitlab_watcher/cache.py
   from datetime import datetime, timedelta
   from typing import Any, Optional, Generic, TypeVar

   T = TypeVar('T')

   class TimedCache(Generic[T]):
       """Simple time-based cache."""

       def __init__(self, ttl_seconds: float = 30.0):
           self._cache: dict[str, tuple[datetime, T]] = {}
           self._ttl = timedelta(seconds=ttl_seconds)

       def get(self, key: str) -> Optional[T]:
           if key in self._cache:
               timestamp, value = self._cache[key]
               if datetime.now() - timestamp < self._ttl:
                   return value
               del self._cache[key]
           return None

       def set(self, key: str, value: T) -> None:
           self._cache[key] = (datetime.now(), value)

       def invalidate(self, key: str) -> None:
           self._cache.pop(key, None)

       def clear(self) -> None:
           self._cache.clear()
   ```

2. **A `GitLabClient` osztály kiegészítése cache-szel:**
   ```python
   class GitLabClient:
       def __init__(self, ..., cache_ttl: float = 30.0) -> None:
           # ... meglévő inicializáció ...
           self._cache = TimedCache[dict](ttl_seconds=cache_ttl)

       def _get_cached(self, key: str) -> Optional[dict]:
           return self._cache.get(key)

       def _set_cached(self, key: str, value: dict) -> None:
           self._cache.set(key, value)

       def get_merge_request(self, project_id: int, mr_iid: int) -> Optional[MergeRequest]:
           cache_key = f"mr_{project_id}_{mr_iid}"
           cached = self._get_cached(cache_key)
           if cached is not None:
               return MergeRequest(**cached)

           # ... API hívás ...
           result = MergeRequest(...)
           self._set_cached(cache_key, {...})
           return result
   ```

3. **A `Watcher.check_mr_status()` optimalizálása:**
   - A cache használatával csökkenteni az API hívásokat
   - Az ETag header használata, ha a GitLab API támogatja

4. **Tesztelés:**
   - Egységtesztek a `TimedCache` osztályhoz
   - Integrációs tesztek a cache-elés ellenőrzésére
   - API hívás számolás a tesztekben

**Érintett fájlok:**
- `src/gitlab_watcher/cache.py` (új)
- `src/gitlab_watcher/gitlab_client.py`
- `src/gitlab_watcher/watcher.py`
- `tests/test_cache.py` (új)
- `tests/test_gitlab_client.py`

---

### 3.2.3. Inefficient State File I/O

**Hely:** `state.py:88-94` - `save()` metódus

**Probléma leírása:**
A `save()` metódus minden alkalommal fájlba ír, amikor a state változik. A `_load_from_file()` pedig mindig fájlból olvas, ha nincs a cache-ben.

**Javítási lépések:**

1. **Debounced mentés implementálása:**
   ```python
   import threading
   from typing import Optional

   class StateManager:
       def __init__(
           self,
           work_dir: Path,
           save_delay: float = 1.0,
       ) -> None:
           self.work_dir = work_dir
           self.work_dir.mkdir(parents=True, exist_ok=True)
           self._states: dict[int, ProjectState] = {}
           self._dirty: set[int] = set()
           self._save_timer: Optional[threading.Timer] = None
           self._save_delay = save_delay
           self._lock = threading.Lock()

       def _schedule_save(self, project_id: int) -> None:
           """Schedule a debounced save operation."""
           with self._lock:
               self._dirty.add(project_id)
               if self._save_timer is not None:
                   self._save_timer.cancel()
               self._save_timer = threading.Timer(
                   self._save_delay,
                   self._flush_dirty,
               )
               self._save_timer.start()

       def _flush_dirty(self) -> None:
           """Save all dirty states."""
           with self._lock:
               for project_id in self._dirty:
                   self._save_sync(project_id)
               self._dirty.clear()
               self._save_timer = None

       def _save_sync(self, project_id: int) -> None:
           """Synchronous save to file."""
           if project_id not in self._states:
               return
           state_file = self._state_file(project_id)
           state_file.write_text(
               json.dumps(asdict(self._states[project_id]), indent=2),
           )

       def save(self, project_id: int) -> None:
           """Schedule a save operation (debounced)."""
           self._schedule_save(project_id)

       def force_save(self, project_id: int) -> None:
           """Immediately save state to file."""
           with self._lock:
               self._dirty.discard(project_id)
               self._save_sync(project_id)

       def force_save_all(self) -> None:
           """Immediately save all dirty states."""
           with self._lock:
               if self._save_timer is not None:
                   self._save_timer.cancel()
               for project_id in self._dirty:
                   self._save_sync(project_id)
               self._dirty.clear()
               self._save_timer = None
   ```

2. **A `Watcher` osztály kiegészítése:**
   - A `force_save_all()` hívása a shutdown során

3. **Tesztelés:**
   - Egységtesztek a debounced mentéshez
   - Integrációs tesztek a state kezeléshez
   - Versenyhelyzet tesztek (concurrent access)

**Érintett fájlok:**
- `src/gitlab_watcher/state.py`
- `src/gitlab_watcher/watcher.py`
- `tests/test_state.py`

---

### 3.2.4. Fázis 2 Tesztelési Stratégia

**Egységtesztek:**
1. `test_timed_cache()` - cache viselkedés tesztelése
2. `test_debounced_save()` - debounced mentés tesztelése
3. `test_connection_pooling()` - connection pooling tesztelése
4. `test_timeout_handling()` - timeout kezelés tesztelése

**Integrációs tesztek:**
1. Teljes API hívás folyamat tesztelése cache-szel
2. State mentés és betöltés tesztelése
3. Hosszú futású tesztek a connection pooling-gal

**Teljesítmény tesztek:**
1. API hívás számának mérése cache előtt és után
2. I/O műveletek számának mérése debounced mentés előtt és után
3. Terheléses tesztek a connection pooling-gal

---

## 3.3. Fázis 3: Architekturális javítások

**Prioritás:** Medium
**Kockázat:** Közepes - Az architektúra változás nagyobb refaktorálást igényelhet
**Függőségek:** Fázis 1 és 2 ajánlott

### 3.3.1. Tight Coupling Between Components

**Hely:** `processor.py` - `Processor` osztály

**Probléma leírása:**
A `Processor` osztály közvetlenül hoz létre `GitOps` példányokat minden metódushívásnál. Ez szoros csatolást okoz és nehezíti a tesztelést.

**Javítási lépések:**

1. **Protocol osztály definiálása:**
   ```python
   # Új fájl: src/gitlab_watcher/protocols.py
   from pathlib import Path
   from typing import Protocol

   class GitOperations(Protocol):
       """Protocol for Git operations."""

       def fetch(self, remote: str = "origin") -> bool: ...
       def checkout(self, branch: str, create: bool = False) -> bool: ...
       def pull(self, remote: str = "origin", branch: str | None = None) -> bool: ...
       def push(
           self,
           remote: str = "origin",
           branch: str | None = None,
           set_upstream: bool = False,
       ) -> bool: ...
       def delete_branch(self, branch: str, force: bool = False) -> bool: ...
       def get_current_branch(self) -> str | None: ...
   ```

2. **A `Processor` osztály módosítása:**
   ```python
   from typing import Callable
   from .protocols import GitOperations

   class Processor:
       def __init__(
           self,
           gitlab: GitLabClient,
           discord: DiscordWebhook,
           state: StateManager,
           gitlab_username: str,
           label_in_progress: str,
           label_review: str,
           claude_mode: str = "ollama",
           claude_custom_command: str = "",
           git_factory: Callable[[Path], GitOperations] = GitOps,
           default_branch: str = "master",
       ) -> None:
           # ... meglévő attribútumok ...
           self.git_factory = git_factory
           self.default_branch = default_branch

       def process_issue(self, project: ProjectConfig, issue: Issue) -> bool:
           git = self.git_factory(project.path)  # Use factory
           # ... a metódus többi része változatlan ...
           git.checkout(self.default_branch)  # Use configurable branch
           # ...
   ```

3. **A konfiguráció kiegészítése:**
   ```python
   # config.py
   @dataclass
   class Config:
       # ... meglévő mezők ...
       default_branch: str = "master"
   ```

4. **Tesztelés:**
   - Egységtesztek mockolt `GitOperations` implementációval
   - Integrációs tesztek a valós `GitOps` osztállyal

**Érintett fájlok:**
- `src/gitlab_watcher/protocols.py` (új)
- `src/gitlab_watcher/processor.py`
- `src/gitlab_watcher/config.py`
- `src/gitlab_watcher/watcher.py`
- `tests/test_processor.py`

**Megjegyzés:** Ez a javítás egyben megoldja a **Hardcoded Branch Name "master"** problémát is (Low priority).

---

### 3.3.2. Fázis 3 Tesztelési Stratégia

**Egységtesztek:**
1. `test_processor_with_mock_git()` - Processor tesztelése mock GitOperations-szel
2. `test_git_factory_injection()` - Git factory injektálás tesztelése
3. `test_default_branch_configuration()` - default branch konfiguráció tesztelése

**Integrációs tesztek:**
1. Teljes folyamat tesztelése a valós GitOps implementációval
2. Konfigurációs tesztek a default branch-sel

---

## 3.4. Fázis 4: Kódminőség javítások

**Prioritás:** Low
**Kockázat:** Alacsony - Ezek a javítások alacsony kockázatúak
**Függőségek:** Nincs, de ajánlott a korábbi fázisok befejezése

### 3.4.1. Missing Type Annotations

**Hely:** Több fájl, különösen `gitlab_client.py`

**Javítási lépések:**
1. Minden publikus metódushoz return type annotation hozzáadása
2. A paraméterek type annotation-jeinek ellenőrzése
3. A `typing` modul használatának egységesítése

**Példa:**
```python
# Előtte
def _request(self, method: str, url: str, **kwargs) -> requests.Response:

# Utána
def _request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
```

---

### 3.4.2. Inconsistent Error Handling Patterns

**Hely:** `gitlab_client.py`

**Javítási lépések:**

1. **Egyedi exception osztályok definiálása:**
   ```python
   # Új fájl: src/gitlab_watcher/exceptions.py
   class GitLabError(Exception):
       """Base exception for GitLab client errors."""
       pass

   class GitLabConnectionError(GitLabError):
       """Network connection error."""
       pass

   class GitLabAPIError(GitLabError):
       """API returned an error response."""
       def __init__(self, status_code: int, message: str):
           self.status_code = status_code
           super().__init__(f"GitLab API error {status_code}: {message}")

   class GitLabNotFoundError(GitLabAPIError):
       """Resource not found (404)."""
       pass

   class GitLabRateLimitError(GitLabAPIError):
       """Rate limit exceeded (429)."""
       pass
   ```

2. **A `GitLabClient` metódusainak egységesítése:**
   - A metódusok dobják a megfelelő exception-t
   - A hívó kód egységesen kezelje az exceptionöket

---

### 3.4.3. Missing Docstrings for Public Methods

**Hely:** Több fájl

**Javítási lépések:**
1. Minden publikus metódushoz docstring hozzáadása
2. A docstring-ek formátumának egységesítése (Google style)
3. A Args, Returns, Raises szekciók kitöltése

---

### 3.4.4. Magic Numbers Without Constants

**Hely:** `processor.py:81` és `git_ops.py:119`

**Javítási lépések:**
1. Konstansok definiálása a modul elején
2. A magic számok helyettesítése konstansokkal

**Példa:**
```python
# processor.py elejére
CLAUDE_CLI_TIMEOUT_SECONDS = 600
MAX_PROMPT_LENGTH = 10000

# git_ops.py elejére
DEFAULT_SLUG_MAX_LENGTH = 30
MAX_BRANCH_NAME_LENGTH = 100
```

---

### 3.4.5. No Logging for Critical Operations

**Hely:** `processor.py`

**Javítási lépések:**
1. Logger hozzáadása a `Processor` osztályhoz
2. A kritikus műveletek naplózása (branch létrehozás, push, MR létrehozás)

**Példa:**
```python
import logging

class Processor:
    def __init__(self, ...):
        # ... meglévő inicializáció ...
        self.logger = logging.getLogger(__name__)

    def process_issue(self, ...):
        self.logger.info(f"[{project.name}] Processing issue #{issue.iid}: {issue.title}")
        # ...
        self.logger.debug(f"[{project.name}] Creating branch: {branch}")
```

---

### 3.4.6. Missing `__all__` in Module Files

**Hely:** Minden forrásfájl

**Javítási lépések:**
1. `__all__` lista definiálása minden modulban
2. Csak a publikus API exportálása

**Példa:**
```python
# processor.py végére
__all__ = ["Processor"]

# gitlab_client.py végére
__all__ = ["GitLabClient", "Issue", "MergeRequest", "Note"]
```

---

### 3.4.7. Fázis 4 Tesztelési Stratégia

**Egységtesztek:**
1. Type annotation ellenőrzés `mypy`-val
2. Exception handling tesztek
3. Docstring formátum ellenőrzés

**Statikus analízis:**
1. `mypy` futtatása a type annotation-ök ellenőrzésére
2. `pylint` futtatása a kódminőség ellenőrzésére
3. `black` és `isort` futtatása a formázás ellenőrzésére

---

## 4. Függőségi Mátrix

| Javítás | Függ tőle | Függ ettől |
|---------|-----------|------------|
| Command Injection | - | Input Validation |
| Sensitive Token Exposure | - | - |
| Input Validation | Command Injection | - |
| Request Timeout | - | Connection Pooling |
| Connection Pooling | Request Timeout | - |
| API Caching | - | - |
| State File I/O | - | - |
| Tight Coupling | - | Hardcoded Branch |
| Type Annotations | - | - |
| Error Handling | - | - |
| Docstrings | - | - |
| Magic Numbers | - | - |
| Hardcoded Branch | Tight Coupling | - |
| Logging | - | - |
| `__all__` | - | - |

---

## 5. Kombinált Javítások

A következő javítások együtt kezelhetők, ami hatékonyabb implementációt tesz lehetővé:

### 5.1. Biztonsági javítások együtt
- **Command Injection + Input Validation**: Mindkettő a bemenet validálásával kapcsolatos, ugyanazt a validációs infrastruktúrát használhatják.

### 5.2. HTTP javítások együtt
- **Request Timeout + Connection Pooling**: Mindkettő a `GitLabClient.__init__()` módosítását igényli, egyszerre implementálható.

### 5.3. Architektúra és konfiguráció együtt
- **Tight Coupling + Hardcoded Branch**: A dependency injection bevezetése lehetőséget ad a default branch konfigurálására.

---

## 6. Implementációs Sorrend

### 6.1. Ajánlott implementációs sorrend

1. **Fázis 1.1: Command Injection** (Critical) - Azonnali biztonsági javítás
2. **Fázis 1.3: Input Validation** (High) - Kapcsolódik a Command Injection-höz
3. **Fázis 1.2: Sensitive Token Exposure** (High) - Önálló biztonsági javítás
4. **Fázis 2.1: Request Timeout + Connection Pooling** (Medium) - Együtt implementálható
5. **Fázis 2.2: API Caching** (Medium) - Önálló teljesítmény javítás
6. **Fázis 2.3: State File I/O** (Medium) - Önálló teljesítmény javítás
7. **Fázis 3.1: Tight Coupling + Hardcoded Branch** (Medium) - Együtt implementálható
8. **Fázis 4: Kódminőség javítások** (Low) - Bármilyen sorrendben

### 6.2. Sprint javaslat

| Sprint | Javítások | Becsült idő |
|--------|-----------|-------------|
| Sprint 1 | Command Injection, Input Validation, Sensitive Token Exposure | 4-6 óra |
| Sprint 2 | Request Timeout, Connection Pooling, API Caching | 3-4 óra |
| Sprint 3 | State File I/O, Tight Coupling, Hardcoded Branch | 3-4 óra |
| Sprint 4 | Type Annotations, Error Handling, Docstrings | 2-3 óra |
| Sprint 5 | Magic Numbers, Logging, `__all__` | 1-2 óra |

---

## 7. Kockázatok és Enyhítések

### 7.1. Fázis 1 kockázatok

| Kockázat | Valószínűség | Hatás | Enyhítés |
|----------|--------------|-------|----------|
| Túl szigorú validáció törli a legitimate használatot | Közepes | Magas | Kiterjedt tesztelés legitimate case-ekkel |
| Biztonsági rés marad a javítás után | Alacsony | Magas | Biztonsági audit, penetration testing |
| Regressziós hibák a validáció miatt | Közepes | Közepes | Automatikus tesztek, code review |

### 7.2. Fázis 2 kockázatok

| Kockázat | Valószínűség | Hatás | Enyhítés |
|----------|--------------|-------|----------|
| Cache inkonzisztencia | Közepes | Közepes | Cache invalidáció implementálása |
| Debounced mentés adatvesztése crash esetén | Alacsony | Magas | Force save shutdownkor |
| Connection pooling hibák hosszú futásnál | Alacsony | Közepes | Connection timeout és cleanup |

### 7.3. Fázis 3 kockázatok

| Kockázat | Valószínűség | Hatás | Enyhítés |
|----------|--------------|-------|----------|
| Refaktorálás megtöri a teszteket | Magas | Közepes | Tesztek frissítése a refaktorálás során |
| Inkompatibilitás a meglévő konfigurációval | Alacsony | Közepes | Backward compatibility biztosítása |

### 7.4. Fázis 4 kockázatok

| Kockázat | Valószínűség | Hatás | Enyhítés |
|----------|--------------|-------|----------|
| Type annotation hibák | Alacsony | Alacsony | mypy futtatása |
| Docstring inkonzisztencia | Alacsony | Alacsony | Docstring linting |

---

## 8. Tesztelési Terv

### 8.1. Teszt kategóriák

| Kategória | Cél | Eszközök |
|-----------|-----|----------|
| Egységtesztek | Egyes komponensek tesztelése | pytest, unittest.mock |
| Integrációs tesztek | Komponensek együttműködése | pytest, requests-mock |
| Biztonsági tesztek | Sebezhetőségek ellenőrzése | Custom scripts, bandit |
| Teljesítmény tesztek | Optimalizációk mérése | pytest-benchmark, time profiling |
| Statikus analízis | Kódminőség ellenőrzése | mypy, pylint, black, isort |

### 8.2. Coverage célok

| Modul | Jelenlegi | Cél |
|-------|-----------|-----|
| processor.py | ~80% | 90%+ |
| watcher.py | ~75% | 85%+ |
| gitlab_client.py | ~85% | 90%+ |
| state.py | ~90% | 95%+ |
| git_ops.py | ~85% | 90%+ |

### 8.3. CI/CD integráció

A javítások után a következő CI/CD lépések javasoltak:

1. **Pre-commit hooks:**
   - black formázás ellenőrzése
   - isort import rendezés ellenőrzése
   - mypy type checking

2. **Pipeline lépések:**
   - Egységtesztek futtatása
   - Coverage report generálása
   - Statikus analízis (pylint, bandit)
   - Biztonsági ellenőrzés (safety, pip-audit)

---

## 9. Összefoglalás

Ez a javítási terv 15 azonosított problémát kezel 4 fázisban, 5 sprintre osztva. A javítások teljes becsült ideje 13-19 óra.

**Kulcsfontosságú javítások:**
1. **Command Injection (Critical)** - Azonnali beavatkozást igényel
2. **Sensitive Token Exposure (High)** - Fontos biztonsági javítás
3. **Input Validation (High)** - A Command Injection-nel együtt kezelendő

**Teljesítmény-optimalizálás:**
- Connection pooling és timeout beállítása
- API response caching
- Debounced state mentés

**Architektúra javítások:**
- Dependency injection bevezetése
- Default branch konfigurálhatósága

**Kódminőség javítások:**
- Type annotations, docstrings, konstansok
- Egységes hibakezelés, naplózás

A terv végrehajtása után a kódbázis biztonságosabb, hatékonyabb és karbantarthatóbb lesz.

---

*Készítette: Senior Business Analyst Agent*
*Dátum: 2026-03-11*