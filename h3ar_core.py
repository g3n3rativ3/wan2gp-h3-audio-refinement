"""Core logic of the H3 Audio Refinement Controls plugin (no Gradio dependency).

The user values (enabled, extra steps, denoising strength, keep audio references) are packed into the existing
MiniMax H3 custom setting ``audio_refinement`` as a short string, for example ``enabled:6:0.5`` or
``enabled:1:0.1:audio_refs``. Keeping the original setting id means the value is saved with the model settings,
in the output metadata, in the queue and is visible to Deepy / MCP like any other custom setting.
Legacy values ``none`` and ``enabled`` (from the original dropdown) are still understood.
"""
import ast
import functools
import inspect
import re
import sys
import textwrap
import threading

SETTING_ID = "audio_refinement"  # = models.minimax_h3.constants.H3_AUDIO_REFINEMENT_SETTING
STEPS_MIN, STEPS_MAX, STEPS_DEFAULT = 1, 6, 6
DENOISE_MIN, DENOISE_MAX, DENOISE_DEFAULT = 0.0, 1.0, 0.5
DENOISE_STEP = 0.01
KEEP_REFS_TOKEN = "audio_refs"
MARKER_KEY = "_h3ar_plugin"  # flags a custom setting definition rewritten by this plugin
VISIBILITY_KEYS = ("video_prompt_type", "audio_prompt_type", "video_prompt_type_not", "audio_prompt_type_not")
SETTING_LABEL = "Audio Refinement Extra Phase (enabled|disabled:extra steps 1-6:denoising 0-1[:audio_refs])"

_TRUE_WORDS = {"enabled", "enable", "on", "true", "yes", "1"}
_FALSE_WORDS = {"none", "disabled", "disable", "off", "false", "no", "0", ""}
_KEEP_REFS_WORDS = {KEEP_REFS_TOKEN, "refs", "audio_ref", "keep", "keep_refs", "1", "true", "on", "yes"}

INFOS_OLD_SENTENCE = "When enabled, the refinement uses 6 extra steps at 0.5 denoising strength."
INFOS_NEW_SENTENCE = ("With the *H3 Audio Refinement Controls* plugin, tick **Audio Refinement Extra Phase** and choose the number of "
                      "**Extra Steps** (1 to 6) and the **Denoising Strength** (0 to 1; 0 skips the phase, 1 fully re-noises the audio; "
                      "the audio itself is re-noised at 3d/(1+2d), e.g. 0.1 -> 25%). The original setting was 6 extra steps at 0.5. "
                      "**Keep Audio References** re-injects the audio references (audio guides, soundtrack excerpts, reference-video "
                      "soundtracks, audio RefMods) into the refinement, so the voice stays anchored to them. "
                      "Through Deepy / the API use `custom_settings.audio_refinement` = `enabled:<steps>:<denoising>[:audio_refs]`, "
                      "for example `enabled:1:0.1:audio_refs`, or `disabled`.")


def _clamp(value, low, high):
    return max(low, min(high, value))


def parse_value(value):
    """Return (enabled, steps, denoise, keep_audio_refs) from any stored representation."""
    enabled, steps, denoise, keep_refs = False, STEPS_DEFAULT, DENOISE_DEFAULT, False
    if value is None:
        return enabled, steps, denoise, keep_refs
    if isinstance(value, bool):
        return value, steps, denoise, keep_refs
    if isinstance(value, dict):
        enabled = bool(value.get("enabled", enabled))
        steps = value.get("steps", steps)
        denoise = value.get("denoise", value.get("denoising", denoise))
        keep_refs = bool(value.get("keep_audio_refs", value.get("audio_refs", keep_refs)))
    else:
        parts = [p for p in re.split(r"[:;,/|\s]+", str(value).strip().lower()) if p != ""]
        if parts:
            flag = parts[0]
            if flag in _TRUE_WORDS:
                enabled = True
            elif flag in _FALSE_WORDS:
                enabled = False
            elif _is_number(flag):  # tolerate "4:0.35" (no flag) as enabled
                enabled, parts = True, ["enabled"] + parts
        if len(parts) > 1:
            steps = parts[1]
        if len(parts) > 2:
            denoise = parts[2]
        keep_refs = any(part in _KEEP_REFS_WORDS for part in parts[3:])
    try:
        steps = int(round(float(steps)))
    except Exception:
        steps = STEPS_DEFAULT
    try:
        denoise = float(denoise)
    except Exception:
        denoise = DENOISE_DEFAULT
    return bool(enabled), _clamp(steps, STEPS_MIN, STEPS_MAX), round(_clamp(denoise, DENOISE_MIN, DENOISE_MAX), 4), bool(keep_refs)


def _is_number(text):
    try:
        float(text)
        return True
    except Exception:
        return False


def encode_value(enabled, steps, denoise, keep_audio_refs=False):
    _, steps, denoise, _ = parse_value({"enabled": True, "steps": steps, "denoise": denoise})
    text = f"{'enabled' if enabled else 'disabled'}:{steps}:{round(denoise, 2):g}"
    return f"{text}:{KEEP_REFS_TOKEN}" if keep_audio_refs else text


def audio_noise_level(denoise):
    """Noise actually mixed into the audio latent: WanGP maps the refinement sigma with the audio shift 3."""
    denoise = float(denoise)
    return 3.0 * denoise / (1.0 + 2.0 * denoise)


DEFAULT_VALUE = encode_value(False, STEPS_DEFAULT, DENOISE_DEFAULT)


# --------------------------------------------------------------------------- model definitions

def _is_original_setting(setting):
    """The original H3 dropdown: id 'audio_refinement' with the choices 'none' / 'enabled'."""
    if not isinstance(setting, dict) or setting.get("id") != SETTING_ID or setting.get(MARKER_KEY):
        return False
    if setting.get("type") != "dropdown":
        return False
    values = {choice[1] if isinstance(choice, (list, tuple)) and len(choice) >= 2 else choice for choice in setting.get("choices", [])}
    return values == {"none", "enabled"}


def _rewrite_setting(setting):
    if not _is_original_setting(setting):
        return setting
    new_setting = {key: setting[key] for key in VISIBILITY_KEYS if key in setting}
    new_setting.update({
        "id": SETTING_ID,
        "name": setting.get("name", "Audio Refinement"),
        "label": SETTING_LABEL,
        "type": "text",
        "default": DEFAULT_VALUE,
        MARKER_KEY: True,
    })
    return new_setting


def patch_model_def(model_def):
    """Rewrite the H3 audio refinement dropdown of a model definition (in place). Returns True if changed."""
    if not isinstance(model_def, dict):
        return False
    settings = model_def.get("custom_settings")
    if not isinstance(settings, list) or not any(_is_original_setting(s) for s in settings):
        return False
    model_def["custom_settings"] = [_rewrite_setting(s) for s in settings]
    infos = model_def.get("infos")
    if isinstance(infos, str) and INFOS_OLD_SENTENCE in infos:
        model_def["infos"] = infos.replace(INFOS_OLD_SENTENCE, INFOS_NEW_SENTENCE)
    return True


def find_plugin_setting(custom_settings):
    """Index and definition of the plugin setting in a normalized custom settings list, or (None, None)."""
    for idx, setting in enumerate(custom_settings or []):
        if isinstance(setting, dict) and setting.get("id") == SETTING_ID and setting.get(MARKER_KEY):
            return idx, setting
    return None, None


def patch_handler(handler_module):
    """Wrap family_handler.query_model_def so that future model definition refreshes are rewritten too."""
    family_handler = getattr(handler_module, "family_handler", None)
    if family_handler is None or getattr(family_handler, "_h3ar_patched", False):
        return False
    original = family_handler.query_model_def

    @functools.wraps(original)
    def query_model_def(base_model_type, model_def):
        result = original(base_model_type, model_def)
        patch_model_def(result)
        return result

    family_handler.query_model_def = staticmethod(query_model_def)
    family_handler._h3ar_patched = True
    return True


# --------------------------------------------------------------------------- UI visibility

UI_CALLERS = {"generate_media_tab", "refresh_custom_settings_visibility"}


def wrap_custom_setting_visible(original):
    """The packed text setting is hidden in WanGP's own custom settings slots (the plugin shows its own
    controls instead) but stays 'visible' everywhere else (video info panel, Deepy, MCP validation)."""
    if getattr(original, "_h3ar_wrapper", False):
        return original

    @functools.wraps(original)
    def custom_setting_visible(setting_def, video_prompt_type="", audio_prompt_type=""):
        if isinstance(setting_def, dict) and setting_def.get(MARKER_KEY):
            frame = sys._getframe(1)
            for _ in range(4):
                if frame is None:
                    break
                if frame.f_code.co_name in UI_CALLERS:
                    return False
                frame = frame.f_back
        return original(setting_def, video_prompt_type, audio_prompt_type)

    custom_setting_visible._h3ar_wrapper = True
    custom_setting_visible._h3ar_original = original
    return custom_setting_visible


# --------------------------------------------------------------------------- pipeline

LOG = "[H3 Audio Refinement]"

_OLD_SCHEDULE = re.compile(
    r"^(?P<indent>[ \t]*)full_steps = round\(refinement_steps / H3_AUDIO_REFINEMENT_DENOISE\)\n"
    r"(?P=indent)refinement_base_sigmas = torch\.linspace\(1\.0, 0\.0, full_steps \+ 1, (?P<kw>[^\n]*?)\)\[-refinement_steps - 1:\]\n",
    re.MULTILINE,
)
# refinement block: prompt encoding without references, then the reference-free payload
_OLD_REFINEMENT_PROMPT = "context, text_tags = self._encode_prompt(input_prompt, [])"
_NEW_REFINEMENT_PROMPT = ("_h3ar_audio_refs = _h3ar_hooks.refinement_audio_refs(self, payload); "
                          "context, text_tags = self._encode_prompt(input_prompt, _h3ar_hooks.refinement_presentation(_h3ar_audio_refs))")
_OLD_REFINEMENT_LORAS = 'active_loras = list(getattr(self.transformer, "_loras_active_adapters", ()))'
_NEW_REFINEMENT_LORAS = "_h3ar_hooks.apply_refinement_audio_refs(payload, _h3ar_audio_refs); " + _OLD_REFINEMENT_LORAS

_refinement_state = {"keep_audio_refs": False}


def _quiet_refmod_fl2va():
    """MiniMax H3 RefMods (FL2VA route) attaches its references to any transformer call whose payload has none.
    The refinement payload has no condition rows for them, which would break the packed layout, so its
    FL2VA staging is switched off for the rest of this generate() call (RefMods clears it at the end anyway)."""
    for module in list(sys.modules.values()):
        state = getattr(module, "__dict__", {}).get("_FL2VA") if module is not None else None
        if isinstance(state, dict) and "active" in state and "entries" in state and state.get("active"):
            state["active"] = False


def collect_audio_refs(payload):
    """From the payload of the last generation pass, return ([audio ref entries], audio condition rows) for every
    audio-bearing reference (audio guides, soundtrack excerpts, reference-video soundtracks, audio RefMods), or None.
    Condition audio rows are laid out as [audio keyframes | references in order]; images carry no audio rows."""
    if not isinstance(payload, dict):
        return None
    refs = payload.get("refs") or []
    rows = payload.get("cond_audio_rows")
    if not refs or rows is None:
        return None
    start = sum(int(k.get("latent_frame_count", 0)) for k in (payload.get("audio_keyframes") or ())) * 2
    kept = []
    for ref in refs:
        audio_t = int(ref.get("ref_audio_t", 0) or 0)
        if ref.get("kind") != "image" and audio_t > 0:
            kept.append({"kind": "audio", "ref_audio_t": audio_t})
    if not kept:
        return None
    total = start + sum(ref["ref_audio_t"] * 2 for ref in kept)
    if total != int(rows.shape[0]):
        print(f"{LOG} audio references not kept: {int(rows.shape[0])} audio condition rows, {total} expected")
        return None
    return kept, rows[start:]


def refinement_audio_refs(pipeline, previous_payload):
    _quiet_refmod_fl2va()
    if not _refinement_state.get("keep_audio_refs"):
        return None
    try:
        audio_refs = collect_audio_refs(previous_payload)
    except Exception as error:
        print(f"{LOG} audio references not kept ({error})")
        return None
    if audio_refs is None:
        print(f"{LOG} no audio reference to keep in the refinement")
    else:
        print(f"{LOG} {len(audio_refs[0])} audio reference(s) kept in the refinement ({int(audio_refs[1].shape[0])} rows)")
    return audio_refs


def refinement_presentation(audio_refs):
    # <Audio N> labels in the same order as in the main passes (video soundtracks and audio refs share the numbering)
    return [{"type": "audio"} for _ in audio_refs[0]] if audio_refs else []


def apply_refinement_audio_refs(payload, audio_refs):
    if audio_refs and isinstance(payload, dict):
        payload["refs"], payload["cond_audio_rows"] = list(audio_refs[0]), audio_refs[1]


def install_generate_rewrite(pipeline_module):
    """Swap the code object of the innermost, undecorated MiniMaxH3Pipeline.generate with a copy that
    - starts the refinement schedule exactly at the chosen strength: linspace(denoise, 0, steps + 1)
      (for the original 6 steps / 0.5 this is numerically identical to WanGP's grid linspace(1, 0, 13)[-7:]);
    - can keep the audio references in the refinement and keeps RefMods' FL2VA hook out of it.
    WanGP's decorators and every wrapper other plugins put around generate (e.g. MiniMax H3 RefMods) stay in place,
    whatever order the plugins are loaded in. Line numbers are preserved. Returns the set of applied features."""
    features = set()
    try:
        cls = pipeline_module.MiniMaxH3Pipeline
        innermost = inspect.unwrap(cls.generate)
        source_file = inspect.getsourcefile(pipeline_module)
        with open(source_file, "r", encoding="utf-8") as reader:
            source = reader.read()
        tree = ast.parse(source)
        node = None
        for class_node in tree.body:
            if isinstance(class_node, ast.ClassDef) and class_node.name == "MiniMaxH3Pipeline":
                for item in class_node.body:
                    if isinstance(item, ast.FunctionDef) and item.name == "generate":
                        node = item
        if node is None or innermost.__code__.co_name != "generate":
            return features
        first_line = min([d.lineno for d in node.decorator_list] + [node.lineno])  # co_firstlineno counts decorators
        if innermost.__code__.co_firstlineno not in (first_line, node.lineno):
            return features
        lines = source.splitlines(keepends=True)[node.lineno - 1:node.end_lineno]  # the def alone, no decorators
        segment = textwrap.dedent("".join(lines))
        new_segment, count = _OLD_SCHEDULE.subn(
            lambda m: (f"{m['indent']}full_steps = refinement_steps\n"
                       f"{m['indent']}refinement_base_sigmas = torch.linspace(float(H3_AUDIO_REFINEMENT_DENOISE), 0.0, refinement_steps + 1, {m['kw']})\n"),
            segment)
        if count == 1:
            segment = new_segment
            features.add("exact_schedule")
        if segment.count(_OLD_REFINEMENT_PROMPT) == 1 and segment.count(_OLD_REFINEMENT_LORAS) == 1 \
                and segment.index(_OLD_REFINEMENT_PROMPT) < segment.index(_OLD_REFINEMENT_LORAS):
            segment = segment.replace(_OLD_REFINEMENT_PROMPT, _NEW_REFINEMENT_PROMPT).replace(_OLD_REFINEMENT_LORAS, _NEW_REFINEMENT_LORAS)
            features.add("audio_refs")
        if not features:
            return features
        code = compile("\n" * (node.lineno - 1) + segment, source_file, "exec")
        namespace = {}
        exec(code, pipeline_module.__dict__, namespace)
        new_code = namespace["generate"].__code__
        if new_code.co_freevars != innermost.__code__.co_freevars:
            return set()
        pipeline_module.__dict__["_h3ar_hooks"] = sys.modules[__name__]
        innermost.__code__ = new_code
        return features
    except Exception as error:
        print(f"{LOG} generate() rewrite unavailable ({error}); using WanGP's step grid, audio references not kept.")
        return set()


_generation_lock = threading.RLock()


def patch_pipeline(pipeline_module):
    cls = getattr(pipeline_module, "MiniMaxH3Pipeline", None)
    if cls is None or getattr(cls, "_h3ar_patched", False):
        return False
    original = cls.generate
    features = install_generate_rewrite(pipeline_module)
    exact_mode = "exact_schedule" in features
    refs_mode = "audio_refs" in features
    try:
        parameters = list(inspect.signature(inspect.unwrap(original)).parameters)
        custom_settings_pos = parameters.index("custom_settings") - 1  # without self
    except Exception:
        custom_settings_pos = None

    @functools.wraps(original)
    def generate(self, *args, **kwargs):
        positional = custom_settings_pos is not None and "custom_settings" not in kwargs and len(args) > custom_settings_pos
        custom_settings = args[custom_settings_pos] if positional else kwargs.get("custom_settings")
        if not isinstance(custom_settings, dict) or SETTING_ID not in custom_settings:
            return original(self, *args, **kwargs)
        enabled, steps, denoise, keep_refs = parse_value(custom_settings.get(SETTING_ID))
        enabled = enabled and steps >= 1 and denoise > 0.0
        new_settings = dict(custom_settings)
        new_settings[SETTING_ID] = "enabled" if enabled else "none"
        if positional:
            args = list(args)
            args[custom_settings_pos] = new_settings
        else:
            kwargs["custom_settings"] = new_settings
        with _generation_lock:
            old_steps = getattr(pipeline_module, "H3_AUDIO_REFINEMENT_STEPS", STEPS_DEFAULT)
            old_denoise = getattr(pipeline_module, "H3_AUDIO_REFINEMENT_DENOISE", DENOISE_DEFAULT)
            old_keep = _refinement_state["keep_audio_refs"]
            if enabled:
                pipeline_module.H3_AUDIO_REFINEMENT_STEPS = int(steps)
                pipeline_module.H3_AUDIO_REFINEMENT_DENOISE = float(denoise)
                _refinement_state["keep_audio_refs"] = bool(keep_refs and refs_mode)
                if keep_refs and not refs_mode:
                    print(f"{LOG} 'Keep Audio References' is unavailable with this WanGP version; refining without references")
                if not exact_mode:
                    effective = steps / max(steps, round(steps / denoise))
                    if abs(effective - denoise) > 1e-6:
                        print(f"{LOG} denoising {denoise:g} rounded to {effective:.3f} by WanGP's step grid")
            try:
                return original(self, *args, **kwargs)
            finally:
                pipeline_module.H3_AUDIO_REFINEMENT_STEPS = old_steps
                pipeline_module.H3_AUDIO_REFINEMENT_DENOISE = old_denoise
                _refinement_state["keep_audio_refs"] = old_keep

    cls.generate = generate
    cls._h3ar_patched = True
    cls._h3ar_exact_schedule = exact_mode
    cls._h3ar_audio_refs = refs_mode
    return True
