"""Runs the print component alone (serve, migrate); the Python render shell lists infra_print.definition() instead."""
from besdk import launcher
from infra_print import definition

launcher.main(definition())
