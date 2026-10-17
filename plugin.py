"""H3 Audio Refinement Controls - WanGP plugin.

Replaces the MiniMax H3 "Audio Refinement Extra Phase" dropdown (None / Enabled 6 steps, denoising 0.5)
with a checkbox, an Extra Steps slider (1-6) and a Denoising Strength slider (0-1).

How it works
- WanGP gives each model at most 5 custom settings and H3 REF2VA already uses 4, so the three values are
  packed into the existing custom setting `audio_refinement` (e.g. "enabled:4:0.35"). They are therefore
  saved with the model settings, in the metadata of the outputs, in the queue, and usable from Deepy / MCP.
- The packed text slot is hidden in the form; this plugin shows its own checkbox + sliders right after the
  custom settings rows and keeps both in sync.
- MiniMaxH3Pipeline.generate is wrapped to apply the chosen steps / denoising strength.
"""
import importlib
import sys
import traceback

import gradio as gr

from shared.utils.plugins import WAN2GPPlugin

from . import h3ar_core as core

LOG = "[H3 Audio Refinement]"
REQUIRED_COMPONENTS = ("state", "video_prompt_type", "audio_prompt_type", "custom_setting_text_inputs", "custom_settings_row")


def _find_wgp_module():
    for name in ("__main__", "wgp"):
        module = sys.modules.get(name)
        if module is not None and hasattr(module, "models_def") and hasattr(module, "get_model_custom_settings"):
            return module
    for module in list(sys.modules.values()):
        if module is not None and hasattr(module, "models_def") and hasattr(module, "generate_media_tab") and hasattr(module, "get_model_custom_settings"):
            return module
    return None


class H3AudioRefinementPlugin(WAN2GPPlugin):
    def __init__(self):
        super().__init__()
        self.name = "H3 Audio Refinement Controls"
        self.version = "1.1.0"
        self.description = "MiniMax H3: checkbox + Extra Steps (1-6) + Denoising Strength (0-1) + Keep Audio References for the Audio Refinement Extra Phase."
        self.type = ["extension"]
        self._wgp = None
        self._visible_fn = None
        self._active = False

    # ------------------------------------------------------------------ setup
    def setup_ui(self):
        for component_id in REQUIRED_COMPONENTS:
            self.request_component(component_id)
        try:
            self._wgp = _find_wgp_module()
            if self._wgp is None:
                print(f"{LOG} WanGP main module not found, plugin inactive.")
                return
            handler = importlib.import_module("models.minimax_h3.minimax_h3_handler")
            core.patch_handler(handler)
            patched = sum(core.patch_model_def(model_def) for model_def in list(self._wgp.models_def.values()))
            current_visible = self._wgp.custom_setting_visible
            self._visible_fn = getattr(current_visible, "_h3ar_original", current_visible)
            self._wgp.custom_setting_visible = core.wrap_custom_setting_visible(current_visible)
            pipeline = importlib.import_module("models.minimax_h3.pipeline")
            core.patch_pipeline(pipeline)
            exact = getattr(pipeline.MiniMaxH3Pipeline, "_h3ar_exact_schedule", False)
            audio_refs = getattr(pipeline.MiniMaxH3Pipeline, "_h3ar_audio_refs", False)
            self._active = True
            print(f"{LOG} active: {patched} MiniMax H3 model definitions updated, {'exact' if exact else 'grid'} denoising schedule, "
                  f"audio references {'available' if audio_refs else 'unavailable'}.")
        except Exception as error:
            self._active = False
            print(f"{LOG} could not be activated: {error}")
            traceback.print_exc()

    # ------------------------------------------------------------------ helpers
    def _current_setting(self, state):
        wgp = self._wgp
        model_type = wgp.get_state_model_type(state)
        model_def = wgp.get_model_def(model_type)
        return core.find_plugin_setting(wgp.get_model_custom_settings(model_def))

    def _visible(self, setting_def, video_prompt_type, audio_prompt_type):
        return bool(self._visible_fn(setting_def, video_prompt_type or "", audio_prompt_type or ""))

    # ------------------------------------------------------------------ UI
    def post_ui_setup(self, components):
        if not self._active:
            return {}
        missing = [component_id for component_id in REQUIRED_COMPONENTS if component_id not in components]
        if missing:
            print(f"{LOG} UI components not found ({', '.join(missing)}), controls not added.")
            return {}
        tab_components = dict(components)  # post_ui_setup runs for the main tab and again for the Edit tab
        self.insert_after("custom_settings_row", lambda: self._build_controls(tab_components))
        return {}

    def _build_controls(self, comps):
        state, video_prompt_type, audio_prompt_type = comps["state"], comps["video_prompt_type"], comps["audio_prompt_type"]
        text_slots = list(comps["custom_setting_text_inputs"])

        visible, (enabled, steps, denoise, keep_refs) = False, core.parse_value(None)
        try:
            idx, setting_def = self._current_setting(state.value)
            if idx is not None and idx < len(text_slots):
                enabled, steps, denoise, keep_refs = core.parse_value(text_slots[idx].value)
                visible = self._visible(setting_def, video_prompt_type.value, audio_prompt_type.value)
        except Exception:
            pass

        def noise_info(strength):
            return f"Audio actually re-noised at ~{core.audio_noise_level(strength) * 100:.0f}%"

        with gr.Column(visible=visible) as box:
            with gr.Row():
                with gr.Column(scale=1, min_width=180):
                    enabled_box = gr.Checkbox(value=enabled, label="Audio Refinement Extra Phase")
                    keep_refs_box = gr.Checkbox(value=keep_refs, label="Keep Audio References (voices, audio RefMods)", interactive=enabled)
                steps_slider = gr.Slider(minimum=core.STEPS_MIN, maximum=core.STEPS_MAX, step=1, value=steps, interactive=enabled,
                                         label="Refinement Extra Steps", scale=2, show_reset_button=False)
                denoise_slider = gr.Slider(minimum=core.DENOISE_MIN, maximum=core.DENOISE_MAX, step=core.DENOISE_STEP, value=denoise, interactive=enabled,
                                           label="Refinement Denoising Strength", info=noise_info(denoise), scale=2, show_reset_button=False)

        no_change = gr.update()
        controls = [enabled_box, keep_refs_box, steps_slider, denoise_slider]

        def sync_from_slots(state_value, video_prompt_type_value, audio_prompt_type_value, *slot_values):
            try:
                idx, setting_def = self._current_setting(state_value)
                if idx is None or idx >= len(slot_values):
                    return gr.update(visible=False), no_change, no_change, no_change, no_change
                is_enabled, n_steps, strength, keep = core.parse_value(slot_values[idx])
                return (gr.update(visible=self._visible(setting_def, video_prompt_type_value, audio_prompt_type_value)),
                        gr.update(value=is_enabled),
                        gr.update(value=keep, interactive=is_enabled),
                        gr.update(value=n_steps, interactive=is_enabled),
                        gr.update(value=strength, interactive=is_enabled, info=noise_info(strength)))
            except Exception as error:
                print(f"{LOG} sync error: {error}")
                return no_change, no_change, no_change, no_change, no_change

        def write_to_slot(state_value, is_enabled, keep, n_steps, strength):
            updates = [no_change] * len(text_slots)
            try:
                idx, _ = self._current_setting(state_value)
                if idx is not None and idx < len(text_slots):
                    updates[idx] = gr.update(value=core.encode_value(bool(is_enabled), n_steps, strength, bool(keep)))
            except Exception as error:
                print(f"{LOG} write error: {error}")
            return updates

        def toggle_enabled(state_value, is_enabled, keep, n_steps, strength):
            interactive = gr.update(interactive=bool(is_enabled))
            return [*write_to_slot(state_value, is_enabled, keep, n_steps, strength), interactive, interactive, interactive]

        gr.on(triggers=[slot.change for slot in text_slots] + [video_prompt_type.change, audio_prompt_type.change],
              fn=sync_from_slots, inputs=[state, video_prompt_type, audio_prompt_type, *text_slots],
              outputs=[box, *controls], show_progress="hidden", queue=False)
        # user-only events (checkbox .input, slider .release = end of drag / number typed), so the
        # programmatic updates made by sync_from_slots never loop back into the slot
        enabled_box.input(fn=toggle_enabled, inputs=[state, *controls],
                          outputs=[*text_slots, keep_refs_box, steps_slider, denoise_slider], show_progress="hidden", queue=False)
        gr.on(triggers=[keep_refs_box.input, steps_slider.release, denoise_slider.release],
              fn=write_to_slot, inputs=[state, *controls],
              outputs=text_slots, show_progress="hidden", queue=False, trigger_mode="always_last")
        return box
