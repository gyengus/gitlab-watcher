"""Click CLI entry point."""

import sys
import click


from pathlib import Path

from .config import DEFAULT_CONFIG_PATH, load_config
from .git_ops import GitOps
from .state import StateManager
from .discord import DiscordWebhook
from .watcher import Watcher


@click.group()
def cli():
    """GitLab Watcher - Monitor projects and process issues/MRs."""
    pass


@cli.command(name="run")
@click.option(
    "--config",
    "-c",
    default=DEFAULT_CONFIG_PATH,
    help="Path to config file",
)
@click.option("--verbose", "-v", is_flag=True, help="Verbose output")
def main(config: str, verbose: bool) -> None:
    """Monitor projects and process issues/MRs."""
    watcher = Watcher(config_path=config, verbose=verbose)
    watcher.run()


@cli.command(name="sync-state")
@click.argument("project_name")
@click.option(
    "--config",
    "-c",
    default=DEFAULT_CONFIG_PATH,
    help="Path to config file",
)
def sync_state(project_name: str, config: str) -> None:
    """Synchronize local git state with remote for *PROJECT_NAME*.

    This command checks whether the current branch of the specified project
    has unpushed commits and attempts a push. It also clears the processing flag
    in ``StateManager`` so that the watcher can continue normal operation.
    """
    # Load configuration
    cfg = load_config(config)
    project = cfg.get_project_by_name(project_name)
    if not project:
        click.echo(f"Project '{project_name}' not found in config.", err=True)
        sys.exit(1)

    git = GitOps(project.path)
    state_work_dir = Path("/tmp/gitlab-watcher")
    import os
    os.makedirs(state_work_dir, mode=0o700, exist_ok=True)
    state = StateManager(state_work_dir)
    discord = DiscordWebhook(project.discord_webhook_url or "")

    # Determine current branch and push if there is unpushed work
    current_branch = git.get_current_branch()
    if current_branch and git.has_unpushed_to_remote():
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
    cli()
