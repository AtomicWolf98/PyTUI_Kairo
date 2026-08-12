"""Settings panel: provider profiles, preferences and theme."""

from __future__ import annotations

from textual.widgets import Static

from kairo_tui_v2.state import AppState


class SettingsPanel(Static):
    """Profiles/preferences overview; mutations go through the kernel."""

    def render_state(self, state: AppState, profiles: tuple[object, ...] = ()) -> None:
        lines = ["[b]Settings[/b]"]
        for profile in profiles:
            label = getattr(profile, "label", "")
            model = getattr(profile, "model", "")
            lines.append(f"• {label} · {model}")
        if not profiles:
            lines.append("No provider profiles configured.")
        lines.append(f"Model label: {state.profile_label or state.model_label}")
        self.update("\n".join(lines))
