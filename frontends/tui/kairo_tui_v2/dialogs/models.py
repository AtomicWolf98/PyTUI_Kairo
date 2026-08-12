"""Model picker: profiles from the kernel catalog; secrets never shown."""

from __future__ import annotations

from kairo_kernel.contracts.providers import ProviderProfile
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, ListItem, ListView, Static


class ModelPicker(ModalScreen[None]):
    """Lists provider profiles; selecting updates the chat profile."""

    BINDINGS = [
        Binding("escape", "cancel_picker", "Cancel", show=False),
    ]

    class ModelChosen(Message):
        def __init__(self, profile_id: object) -> None:
            super().__init__()
            self.profile_id = profile_id

    class ConnectRequested(Message):
        pass

    def compose(self) -> ComposeResult:
        yield Static("Models", id="models-title")
        yield ListView(id="model-list")
        yield Button("Connect another model…", id="model-connect")
        yield Button("Cancel", id="model-cancel")

    def on_mount(self) -> None:
        self.query_one("#model-list", ListView).focus()

    def set_profiles(self, profiles: tuple[ProviderProfile, ...]) -> None:
        items: list[ListItem] = []
        self._profiles: list[ProviderProfile] = []
        for profile in profiles:
            label = f"{profile.label} · {profile.model}"
            items.append(ListItem(Static(label)))
            self._profiles.append(profile)
        list_view = self.query_one("#model-list", ListView)
        list_view.clear()
        list_view.extend(items)
        if items:
            list_view.index = 0

    @on(ListView.Selected, "#model-list")
    def on_selected(self, message: ListView.Selected) -> None:
        index = self.query_one("#model-list", ListView).index
        if index is not None and index < len(self._profiles):
            self.post_message(self.ModelChosen(self._profiles[index].profile_id))

    @on(Button.Pressed, "#model-connect")
    def on_connect(self, message: Button.Pressed) -> None:
        self.post_message(self.ConnectRequested())

    @on(Button.Pressed, "#model-cancel")
    def on_cancel(self, message: Button.Pressed) -> None:
        self.action_cancel_picker()

    def action_cancel_picker(self) -> None:
        self.dismiss(None)
