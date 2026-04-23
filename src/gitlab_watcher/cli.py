"""Click CLI entry point."""

import warnings
import sys

# Suppress the specific urllib3 dependency warning
# This warning is harmless and doesn't affect functionality
warnings.filterwarnings("ignore", category=UserWarning, message=".*urllib3.*chardet.*charset-normalizer.*")

import click

from .config import DEFAULT_CONFIG_PATH, ConfigLoader
from .git_ops import GitOps
from .state import StateManager
from .discord import DiscordWebhook
from .watcher import Watcher


@click.command(name="run")
@click.option(
    "--config",
    "-c",
    default=DEFAULT_CONFIG_PATH,
    help="Path to config file",
)
@click.option("--verbose", "-v", is_flag=True, help="Verbose output")
def main(config: str, verbose: bool) -> None:
    """GitLab Watcher - Monitor projects and process issues/MRs."""
    watcher = Watcher(config_path=config, verbose=verbose)
    watcher.run()


@click.command(name="sync-state")
@click.argument("project_name")
def sync_state(project_name: str) -> None:
    """Synchronize local git state with remote for *PROJECT_NAME*.

    This command checks whether the current branch of the specified project
    has unpushed commits and attempts a push. It also clears the processing flag
    in ``StateManager`` so that the watcher can continue normal operation.
    """
    # Load configuration
    cfg = ConfigLoader.load()
    project = cfg.get_project_by_name(project_name)
    if not project:
        click.echo(f"Project '{project_name}' not found in config.", err=True)
        sys.exit(1)

    git = GitOps(project.path)
    state = StateManager(project.project_id)
    discord = DiscordWebhook(project.discord_webhook_url or "")

    # Determine current branch and push if there is unpushed work
    current_branch = git.get_current_branch()
    if current_branch and git.has_unpushed_work(project.default_branch):
        pushed = git.push("origin", current_branch)
        if pushed:
            # Reset processing flag – the watcher treats the repo as clean now
            state.set_processing(project.project_id, False)
            click.echo(f"Pushed unpushed work on branch '{current_branch}'.")
        else:
            discord.notify_error(
                project.name,
                f"Failed to push unpushed work on branch '{current_branch}'.",
                details="Sync-state command could not push changes to remote.",
            )
            click.echo("Push failed – see Discord for details.", err=True)
    else:
        click.echo("No unpushed work detected; state is already synchronized.")


if __name__ == "__main__":
    # When executed directly, expose both commands via a simple CLI group.
    # ``click`` will automatically create a ``click.Group`` when multiple commands
    # are defined in the same module and the module is executed as a script.
    # This keeps backward compatibility with the original ``gitlab-watcher`` entry
    # point while providing the new ``sync-state`` sub-command.
    cli = click.Group()
    cli.add_command(main)
    cli.add_command(sync_state)
    cli()