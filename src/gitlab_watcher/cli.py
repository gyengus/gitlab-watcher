"""Click CLI entry point."""

import os
import sys
import click


from pathlib import Path

from .config import DEFAULT_CONFIG_PATH, Config, load_config
from .git_ops import GitOps
from .state import StateManager
from .discord import DiscordWebhook
from .watcher import Watcher


@click.group(invoke_without_command=True)
@click.option(
    "--config",
    "-c",
    default=DEFAULT_CONFIG_PATH,
    help="Path to config file",
)
@click.option("--verbose", "-v", is_flag=True, help="Verbose output")
@click.pass_context
def cli(ctx, config, verbose):
    """GitLab Watcher - Monitor projects and process issues/MRs."""
    if ctx.invoked_subcommand is None:
        # If no subcommand, run the main watcher logic
        try:
            watcher = Watcher(config_path=config, verbose=verbose)
            watcher.run()
        except (FileNotFoundError, ValueError, RuntimeError) as e:
            click.echo(f"Error: {e}", err=True)
            sys.exit(1)
        except Exception as e:
            if verbose:
                raise e
            click.echo(f"An unexpected error occurred: {e}", err=True)
            sys.exit(1)


@cli.command(name="run")
@click.option(
    "--config",
    "-c",
    default=DEFAULT_CONFIG_PATH,
    help="Path to config file",
)
@click.option("--verbose", "-v", is_flag=True, help="Verbose output")
def run_command(config: str, verbose: bool) -> None:
    """Monitor projects and process issues/MRs (same as default)."""
    try:
        watcher = Watcher(config_path=config, verbose=verbose)
        watcher.run()
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        if verbose:
            raise e
        click.echo(f"An unexpected error occurred: {e}", err=True)
        sys.exit(1)


def _init_components(config_path: str) -> tuple[Config, StateManager]:
    """Helper to initialize shared components for CLI commands."""
    cfg = load_config(config_path)
    # Ensure work directory exists with restricted permissions
    work_dir = Path("/tmp/gitlab-watcher")
    os.makedirs(work_dir, mode=0o700, exist_ok=True)
    state = StateManager(work_dir)
    return cfg, state


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
    try:
        # Load configuration and state
        cfg, state = _init_components(config)
        project = cfg.get_project_by_name(project_name)
        if not project:
            click.echo(f"Project '{project_name}' not found in config.", err=True)
            sys.exit(1)

        git = GitOps(project.path)
        discord = DiscordWebhook(cfg.discord_webhook)

        # Always clear processing flag to allow manual recovery from crashes
        state.set_processing(project.project_id, False)

        # Determine current branch and push if there is unpushed work
        current_branch = git.get_current_branch()
        if current_branch and git.has_unpushed_commits(current_branch):
            pushed = git.push("origin", current_branch)
            if pushed:
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
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"An unexpected error occurred: {e}", err=True)
        sys.exit(1)


def main():
    """Entry point for the gitlab-watcher command."""
    cli()


if __name__ == "__main__":
    main()
