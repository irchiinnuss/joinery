"""Joinery Remote: follow and direct Journeyman from a phone (ADR 0093).

The phone talks to one per-user server (:mod:`joinery.remote.server`) that listens on
loopback only and is reached through the user's own Tailscale (``tailscale serve``).
The server talks to each project's chat host over a local-only channel
(:mod:`bridge.remote_channel`), so a phone action runs the same code as the sidebar's.
"""
