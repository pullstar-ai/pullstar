"""Packaged, versioned system prompts for the PullStar brief.

Each ``brief_v<N>.txt`` (or ``brief_<style>_v<N>.txt``) is one prompt version,
shipped as package data. Select one on the CLI with ``--prompt brief_v2`` or
via :func:`pullstar.resources.resolve_brief_prompt`. Read files here through
:mod:`pullstar.resources`, never by filesystem path.

Contributing a prompt: add a new ``.txt`` file here (do not edit an existing
version in place — bump the version in the filename) and open a pull request.
See ``pullstar/prompts/README.md``.
"""
