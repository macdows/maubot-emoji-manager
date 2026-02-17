import asyncio
import re
from typing import Type

from maubot import Plugin, MessageEvent
from maubot.handlers import command
from mautrix.types import EventType, RoomID
from mautrix.util.config import BaseProxyConfig, ConfigUpdateHelper


EMOTES_TYPE = EventType("im.ponies.room_emotes", EventType.Class.STATE)
SHORTCODE_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
SHORTCODE_MAX_BYTES = 100


def serialize_content(content) -> dict:
    if hasattr(content, "serialize"):
        return content.serialize()
    if isinstance(content, dict):
        return content
    return {}


def get_images(content: dict) -> dict:
    return content.get("images") or content.get("emoticons") or {}


def get_pack_meta(content: dict) -> dict:
    return content.get("pack") or {}


def validate_shortcode(shortcode: str) -> tuple[bool, str]:
    if not SHORTCODE_RE.match(shortcode):
        return False, "Shortcode must only contain letters, numbers, hyphens, and underscores."
    if len(shortcode.encode("utf-8")) > SHORTCODE_MAX_BYTES:
        return False, f"Shortcode must be {SHORTCODE_MAX_BYTES} bytes or less."
    return True, ""


def build_pack_content(images: dict, pack_meta: dict) -> dict:
    content = {"images": images}
    if pack_meta:
        content["pack"] = pack_meta
    return content


class Config(BaseProxyConfig):
    def do_update(self, helper: ConfigUpdateHelper) -> None:
        helper.copy("presets")
        helper.copy("rooms")
        helper.copy("allowed_users")
        helper.copy("delay")


class EmojiManager(Plugin):

    async def start(self) -> None:
        self.config.load_and_update()
        self._cancel = False
        self._task: asyncio.Task | None = None

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()

    @classmethod
    def get_config_class(cls) -> Type[BaseProxyConfig]:
        return Config

    def _is_allowed(self, sender: str) -> bool:
        allowed = self.config["allowed_users"] or []
        return not allowed or sender in allowed

    async def _resolve_room(self, room: str) -> RoomID:
        if room.startswith("#"):
            resp = await self.client.resolve_room_alias(room)
            return resp.room_id
        return RoomID(room)

    async def _read_pack(self, room_id) -> tuple[dict, dict]:
        try:
            raw = await self.client.get_state_event(room_id, EMOTES_TYPE, "")
            content = serialize_content(raw)
        except Exception:
            content = {}
        return get_images(content), get_pack_meta(content)

    def _validate_preset(self, name: str) -> tuple[dict | None, dict, list[str], str | None]:
        """Validate a preset by name. Returns (images, pack_meta, warnings, error)."""
        presets = self.config["presets"] or {}

        if name not in presets:
            return None, {}, [], f"Unknown preset `{name}`."

        preset_data = presets[name]
        if not isinstance(preset_data, dict) or "images" not in preset_data:
            return None, {}, [], f"Preset `{name}` is misconfigured (missing `images`)."

        raw_images = preset_data["images"]
        images = {}
        warnings = []
        for shortcode, entry in raw_images.items():
            valid, reason = validate_shortcode(shortcode)
            if not valid:
                warnings.append(f"Skipped `{shortcode}`: {reason}")
                continue
            if not isinstance(entry, dict) or not entry.get("url", "").startswith("mxc://"):
                warnings.append(f"Skipped `{shortcode}`: invalid or missing mxc:// URL")
                continue
            images[shortcode] = {"url": entry["url"]}

        if not images:
            return None, {}, warnings, f"Preset `{name}` has no valid emojis."

        pack_meta = preset_data.get("pack") or {}
        return images, pack_meta, warnings, None

    async def _bulk_preset(self, evt: MessageEvent, preset_name: str, images: dict, pack_meta: dict, rooms: list[str]) -> None:
        delay = self.config["delay"] or 0.5
        display_name = pack_meta.get("display_name")
        applied = 0
        skipped = 0
        errors = []

        for room in rooms:
            if self._cancel:
                break

            try:
                room_id = await self._resolve_room(room)
            except Exception as e:
                errors.append(f"{room}: failed to resolve — {e}")
                continue

            try:
                current_images, current_pack = await self._read_pack(room_id)
                if display_name and current_pack.get("display_name") == display_name and current_images == images:
                    skipped += 1
                    continue

                await self.client.send_state_event(
                    room_id, EMOTES_TYPE,
                    build_pack_content(images, pack_meta),
                    state_key="",
                )
                applied += 1
            except Exception as e:
                errors.append(f"{room}: {e}")

            await asyncio.sleep(delay)

        cancelled = " (cancelled)" if self._cancel else ""
        msg = f"Bulk preset `{preset_name}` done{cancelled}: {applied} applied, {skipped} skipped"
        if errors:
            msg += f", {len(errors)} errors:\n" + "\n".join(errors)
        await evt.reply(msg)
        self._task = None

    @command.new("emoji", help="Manage custom emojis. Subcommands: add, remove, list, preset, bulk-preset, cancel")
    async def emoji(self, evt: MessageEvent) -> None:
        pass

    @emoji.subcommand("add", help="Add emoji — !emoji add <shortcode> <mxc_url>")
    @command.argument("shortcode", required=True)
    @command.argument("mxc_url", required=True)
    async def add_emoji(self, evt: MessageEvent, shortcode: str, mxc_url: str) -> None:
        if not self._is_allowed(evt.sender):
            await evt.reply("You are not allowed to use this command.")
            return

        valid, reason = validate_shortcode(shortcode)
        if not valid:
            await evt.reply(reason)
            return

        if not mxc_url.startswith("mxc://"):
            await evt.reply("Invalid MXC URL. Must start with mxc://")
            return

        try:
            images, pack_meta = await self._read_pack(evt.room_id)
            images[shortcode] = {"url": mxc_url}

            await self.client.send_state_event(
                evt.room_id, EMOTES_TYPE,
                build_pack_content(images, pack_meta),
                state_key="",
            )
            await evt.reply(f"Added emoji :{shortcode}:")
        except Exception as e:
            await evt.reply(f"Error adding emoji: {e}")

    @emoji.subcommand("remove", help="Remove emoji — !emoji remove <shortcode>")
    @command.argument("shortcode", required=True)
    async def remove_emoji(self, evt: MessageEvent, shortcode: str) -> None:
        if not self._is_allowed(evt.sender):
            await evt.reply("You are not allowed to use this command.")
            return

        try:
            images, pack_meta = await self._read_pack(evt.room_id)

            if shortcode not in images:
                await evt.reply(f"Emoji :{shortcode}: not found")
                return

            del images[shortcode]

            await self.client.send_state_event(
                evt.room_id, EMOTES_TYPE,
                build_pack_content(images, pack_meta),
                state_key="",
            )
            await evt.reply(f"Removed emoji :{shortcode}:")
        except Exception as e:
            await evt.reply(f"Error: {e}")

    @emoji.subcommand("list", help="List all custom emojis in this room")
    async def list_emojis(self, evt: MessageEvent) -> None:
        if not self._is_allowed(evt.sender):
            await evt.reply("You are not allowed to use this command.")
            return

        try:
            images, _ = await self._read_pack(evt.room_id)

            if not images:
                await evt.reply("No custom emojis in this room")
                return

            emoji_list = "\n".join(
                f":{name}: — {data['url']}" for name, data in images.items()
            )
            await evt.reply(f"Custom emojis:\n{emoji_list}")
        except Exception as e:
            self.log.error(f"Failed to list emojis: {e}")
            await evt.reply("No custom emojis in this room")

    @emoji.subcommand("preset", help="Apply emoji preset — !emoji preset [name]")
    @command.argument("name", required=False)
    async def preset(self, evt: MessageEvent, name: str) -> None:
        if not self._is_allowed(evt.sender):
            await evt.reply("You are not allowed to use this command.")
            return

        presets = self.config["presets"] or {}

        if not name:
            if not presets:
                await evt.reply("No presets configured.")
                return
            names = ", ".join(sorted(presets.keys()))
            await evt.reply(f"Available presets: {names}")
            return

        images, pack_meta, warnings, error = self._validate_preset(name)
        if error:
            await evt.reply(error)
            return

        try:
            await self.client.send_state_event(
                evt.room_id, EMOTES_TYPE,
                build_pack_content(images, pack_meta),
                state_key="",
            )
            msg = f"Applied preset `{name}` ({len(images)} emojis)."
            if warnings:
                msg += "\nWarnings:\n" + "\n".join(warnings)
            await evt.reply(msg)
        except Exception as e:
            await evt.reply(f"Error applying preset: {e}")

    @emoji.subcommand("bulk-preset", help="Apply preset to all configured rooms — !emoji bulk-preset <name>")
    @command.argument("name", required=True)
    async def bulk_preset(self, evt: MessageEvent, name: str) -> None:
        if not self._is_allowed(evt.sender):
            await evt.reply("You are not allowed to use this command.")
            return

        if self._task and not self._task.done():
            await evt.reply("A bulk operation is already running. Use `!emoji cancel` to stop it.")
            return

        rooms = self.config["rooms"] or []
        if not rooms:
            await evt.reply("No rooms configured.")
            return

        images, pack_meta, warnings, error = self._validate_preset(name)
        if error:
            await evt.reply(error)
            return

        msg = f"Starting bulk preset `{name}` across {len(rooms)} rooms..."
        if warnings:
            msg += "\nWarnings:\n" + "\n".join(warnings)
        await evt.reply(msg)

        self._cancel = False
        self._task = asyncio.create_task(
            self._bulk_preset(evt, name, images, pack_meta, rooms)
        )

    @emoji.subcommand("cancel", help="Cancel a running bulk operation")
    async def cancel(self, evt: MessageEvent) -> None:
        if not self._is_allowed(evt.sender):
            await evt.reply("You are not allowed to use this command.")
            return

        if not self._task or self._task.done():
            await evt.reply("No bulk operation is running.")
            return

        self._cancel = True
        await evt.reply("Cancelling bulk operation...")
