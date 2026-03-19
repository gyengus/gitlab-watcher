# Running Tests Locally

If your operating system enforces PEP-668 and prevents global package installation with an `externally-managed-environment` error, the safest and recommended way to execute the GitLab Watcher test suite is to isolate the testing dependencies inside a Python virtual environment (`venv`).

## Step-by-Step Guide

Run the following commands in the terminal from the root directory of the project:

### 1. Create the virtual environment
Create a dedicated local environment folder (named `.venv` by convention):
```bash
python3 -m venv .venv
```

### 2. Install the project and dependencies
Use the virtual environment's `pip` directly. This bypasses any terminal environment issues where `source activate` might fail to override your system's `pip`.

```bash
.venv/bin/pip install -e ".[dev]" pytest pytest-cov
```

*(Note: If it says `.venv/bin/pip` is missing, your system might be missing the full venv package. Run `sudo apt install python3-venv` or `sudo apt install python3-full` first, then recreate the `.venv` folder).*

### 3. Run the test suite
With everything installed in the virtual environment, execute the tests using the isolated pytest:
```bash
.venv/bin/pytest tests/
```

## Exiting the Virtual Environment
Once you are done developing or running tests, you can safely exit the virtual environment by running:
```bash
deactivate
```
This command unbinds the isolated PATH and returns your terminal to its normal OS state.
