import re
from typing import Type

from maubot import Plugin, MessageEvent
from maubot.handlers import command
from mautrix.types import EventType
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


class EmojiManager(Plugin):

    async def start(self) -> None:
        self.config.load_and_update()

    @classmethod
    def get_config_class(cls) -> Type[BaseProxyConfig]:
        return Config

    async def _read_pack(self, room_id) -> tuple[dict, dict]:
        try:
            raw = await self.client.get_state_event(room_id, EMOTES_TYPE, "")
            content = serialize_content(raw)
        except Exception:
            content = {}
        return get_images(content), get_pack_meta(content)

    @command.new("emoji", help="Manage custom emojis. Subcommands: add, remove, list, preset")
    async def emoji(self, evt: MessageEvent) -> None:
        pass

    @emoji.subcommand("add", help="Add emoji — !emoji add <shortcode> <mxc_url>")
    @command.argument("shortcode", required=True)
    @command.argument("mxc_url", required=True)
    async def add_emoji(self, evt: MessageEvent, shortcode: str, mxc_url: str) -> None:
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
                evt.room_id,
                EMOTES_TYPE,
                build_pack_content(images, pack_meta),
                state_key="",
            )
            await evt.reply(f"Added emoji :{shortcode}:")
        except Exception as e:
            await evt.reply(f"Error adding emoji: {e}")

    @emoji.subcommand("remove", help="Remove emoji — !emoji remove <shortcode>")
    @command.argument("shortcode", required=True)
    async def remove_emoji(self, evt: MessageEvent, shortcode: str) -> None:
        try:
            images, pack_meta = await self._read_pack(evt.room_id)

            if shortcode not in images:
                await evt.reply(f"Emoji :{shortcode}: not found")
                return

            del images[shortcode]

            await self.client.send_state_event(
                evt.room_id,
                EMOTES_TYPE,
                build_pack_content(images, pack_meta),
                state_key="",
            )
            await evt.reply(f"Removed emoji :{shortcode}:")
        except Exception as e:
            await evt.reply(f"Error: {e}")

    @emoji.subcommand("list", help="List all custom emojis in this room")
    async def list_emojis(self, evt: MessageEvent) -> None:
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
        presets = self.config["presets"] or {}

        if not name:
            if not presets:
                await evt.reply("No presets configured.")
                return
            names = ", ".join(sorted(presets.keys()))
            await evt.reply(f"Available presets: {names}")
            return

        if name not in presets:
            await evt.reply(f"Unknown preset `{name}`.")
            return

        preset_data = presets[name]
        if not isinstance(preset_data, dict) or "images" not in preset_data:
            await evt.reply(f"Preset `{name}` is misconfigured (missing `images`).")
            return

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
            await evt.reply(f"Preset `{name}` has no valid emojis.")
            return

        pack_meta = preset_data.get("pack") or {}
        try:
            await self.client.send_state_event(
                evt.room_id,
                EMOTES_TYPE,
                build_pack_content(images, pack_meta),
                state_key="",
            )
            msg = f"Applied preset `{name}` ({len(images)} emojis)."
            if warnings:
                msg += "\nWarnings:\n" + "\n".join(warnings)
            await evt.reply(msg)
        except Exception as e:
            await evt.reply(f"Error applying preset: {e}")
