"""Click CLI entry point."""

import click

from .watcher import Watcher


@click.command()
@click.option(
    "--config",
    "-c",
    default="~/.claude/config/gitlab.conf",
    help="Path to config file",
)
@click.option("--verbose", "-v", is_flag=True, help="Verbose output")
def main(config: str, verbose: bool) -> None:
    """GitLab Watcher - Monitor projects and process issues/MRs."""
    watcher = Watcher(config_path=config, verbose=verbose)
    watcher.run()


if __name__ == "__main__":
    main()