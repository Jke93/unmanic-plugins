import json;
import logging;
import subprocess;
from typing import List, Dict, Any

from unmanic.libs.unplugins.settings import PluginSettings

logger = logging.getLogger(__name__)


class Settings(PluginSettings):
    """
    PluginSettings für dieses Plugin
    Wird von Unmanic verwendet, um im WebUI ein Einstellungsformular anzubieten.
    Dokumentation: Accessing Plugin Settings https://docs.unmanic.app/docs/development/writing_plugins/plugin_settings
    """

    # keys -> werden im WebUI als Beschriftung verwendet
    settings = {
        # Sprach-Tags auf die geprüft werden soll (in der Praxis: de, deu, ger)
        "Preferred language tags (comma separated)": "deu,de,ger",

        # Soll TrueHD als 'guter' Codec zählen? (zusätzlich zu AC3/EAC3)
        "Treat Dolby TrueHD as good codec": True
    }

def _parse_lang_tags(raw: str) -> List[str]:
    """
    Zerlegt eine durch Komma getrennte Liste von Sprach-Tags in eine normalisierte Kleibuchstaben-Liste.
    """

    if not raw:
        return[]
    return [t.strip().lower() for t in raw.split(",") if t.strip()]


def _ffprobe_audio_streams(path: str) -> List[Dict[str, Any]]:
    """
    Ruft ffprobe auf und gibt die Audio-Streams als Liste von Dicts zurück.
    Nutzt ein minimalistisches JSON-Schema: codec_name, codec_type, tags.language.
    """

    cmd = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "a",
        "-show_entries", "stream=index,codec_name,codec_type:stream_tags=language",
        "-of", "json",
        path,
    ]

    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            text=True,
        )
    except Exception as e:
        logger.debug("ffprobe failed for %s: %s", path, e)
        return []
    
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        logger.debug("ffprobe JSON decode failed for %s: %s", path, e)
        return []
    
    streams = data.get("streams") or []
    # Nur zur Sicherheit (sollten ohnehin nur Audio-Streams sein durch -select_streams a)
    return [s for s in streams if s.get("codec_type") == "audio"]

def _has_de_dts_and_no_de_good(
        streams: List[Dict[str, Any]],
        lang_tags: List[str],
        treat_truehd_as_good: bool,
) -> bool:
    """
    Prüft, ob:
    - es mindestens eine DTS-basierte Spur (codec_name beginnt mit 'dts') in den angegebenen Sprachen gibt
    - es KEINE Spure mit codec_name in {ac3, eac3, [truehd]} in den angegebenen Sprachen gibt
    """

    has_de_dts = False
    has_de_good = False

    good_codecs = {"ac3", "eac3"}
    if treat_truehd_as_good:
        good_codecs.add("truehd")

    for stream in streams:
        codec = (stream.get("codec_name") or "").lower()
        tags = stream.get("tags") or {}
        lang = (tags.get("language") or "").lower()

        # Wenn ein Sprach-Filter konfiguriert ist, nur diese Sprachen betrachten
        if lang_tags and lang not in lang_tags:
            continue

        if codec.startswith("dts"):
            has_de_dts = True

        if codec in good_codecs:
            has_de_good = True

    return has_de_dts and not has_de_good


def on_library_management_file_test(data: Dict[str, Any]) -> None:
    """
    Runner-Funktion für den Library-File-Test.

    Laut Dokumentation enhält 'data' u. a.: library-id, path, issues, add_file_to_pending_tasks,
    priority_score, shared_info. Dieses Schema darf nicht geändert werden. :contentReference[oaicite:7]{index=7}
    """

    # Einstellungen für die spezifische Library laden
    settings = Settings(library_id=data.get("library_id"))

    lang_raw = settings.get_setting("Preferred language tags (comma separated)")
    lang_tags = _parse_lang_tags(lang_raw)

    treat_truehd = bool(settings.get_setting("Treat Dolby TrueHD as good codec"))

    path = data.get("path")
    if not path:
        # Keine sinnvolle Entscheidung möglich - nichts verändern, nur Issue anhängen
        data["issues"].append({
            "id": "de_dts_without_de_ac3_eac3_truehd",
            "message": "Kein Dateipfad im Plugin-Runner verfügbar.",
        })
        return
    
    streams = _ffprobe_audio_streams(path)

    if not streams:
        # ffprobe-Fehler oder keine Audio-Streams - Entscheidung der übrigen Tests überlassen.
        data["issues"].append({
            "id": "de_dts_without_de_ac3_eac3_truehd",
            "message": "ffprobe lieferte keine Audio-Streams - Datei wird nicht explizit gefiltert.",
        })
        return
    
    candidate = _has_de_dts_and_no_de_good(
        streams=streams,
        lang_tags=lang_tags,
        treat_truehd_as_good=treat_truehd,
    )

    plugin_id = "de_dts_without_de_ac3_eac3_truehd"
    
    if candidate:
        # Nur in diesem Fall explizit zur Verarbeitung markieren
        data["add_file_to_pending_tasks"] = True
        data["issues"].append({
            "id": plugin_id,
            "message": "Datei enthält DE-DTS, aber keine DE-AC3/EAC3/TrueHD - für EAC3-Transcode zulassen.",
        })
    else:
        # Alles andere explizit blocken:
        # - keine passende DE-DTS-Spur
        # - oder bereits eine DE-AC3/EAC3/TrueHD-Spur vorhanden
        data["add_file_to_pending_tasks"] = False
        data["issues"].append({
            "id": plugin_id,
            "message": "Datei ignoriert: Entweder keine DE-DTS-Spur oder bereits DE-AC3/EAC3/TrueHD vorhanden.",
        })

    # 'data' sonst unverändert zurückgeben
    return