# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender UI: properties, operators, panels and preferences."""

import os

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, IntProperty,
                       PointerProperty, StringProperty)
from bpy.types import AddonPreferences, Menu, Operator, Panel, PropertyGroup

import sys

from . import ai, build, compiler, examples, mcp_setup
from .lang import FormulaError

ADDON_ID = __package__
VERSION = "0.0.0"   # set by __init__.register()


# ═════════════════════════════════════════════════════════════════════════════
#  Helpers
# ═════════════════════════════════════════════════════════════════════════════

def geo_space(context):
    space = getattr(context, "space_data", None)
    if space and space.type == "NODE_EDITOR" and space.tree_type == "GeometryNodeTree":
        return space
    return None


def prefs(context=None):
    context = context or bpy.context
    addon = context.preferences.addons.get(ADDON_ID)
    return addon.preferences if addon else None


def settings(context):
    return context.scene.formula_settings


def script_text(s):
    if s.script_source == "TEXT":
        return s.script_text.as_string() if s.script_text else ""
    return "\n".join(line.text for line in s.script_lines)


def set_script_text(s, text):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if s.script_source == "TEXT":
        if s.script_text is None:
            s.script_text = bpy.data.texts.new("Formula Script")
        s.script_text.from_string(text)
    else:
        s.script_lines.clear()
        for raw in text.split("\n"):
            s.script_lines.add().text = raw.rstrip()


def set_status(s, level, message, line=-1):
    s.status = message
    s.status_level = level
    s.error_line = line if line else -1
    if line and line > 0 and s.script_source == "TEXT" and s.script_text:
        s.script_text.current_line_index = line - 1


def active_formula_group(space):
    tree = space.edit_tree if space else None
    node = tree.nodes.active if tree else None
    if node and node.bl_idname == "GeometryNodeGroup" and build.is_formula_group(node.node_tree) \
            and node.node_tree.library is None:
        return node
    return None


def tag_redraw_node_editors():
    wm = bpy.context.window_manager
    for win in wm.windows:
        for area in win.screen.areas:
            if area.type == "NODE_EDITOR":
                area.tag_redraw()


def compile_and_build(context, space, source, mode, output, report):
    """Compile, then rebuild the active formula group or add a new one.
    Returns True on success. All user feedback goes through status + report."""
    s = settings(context)
    try:
        result = compiler.compile_source(source, mode, output)
    except FormulaError as e:
        set_status(s, "ERROR", str(e), e.line or -1)
        report({"ERROR"}, str(e))
        return False

    target_node = active_formula_group(space) if s.replace_active else None
    try:
        if target_node:
            tree = build.build_group(result, source, mode, output, target=target_node.node_tree, version=VERSION)
            verb = "Updated"
        else:
            tree = build.build_group(result, source, mode, output, name=s.group_name or "Formula", version=VERSION)
            verb = "Created"
            if s.auto_add and space and space.edit_tree and space.edit_tree != tree:
                edit = space.edit_tree
                for n in edit.nodes:
                    n.select = False
                gn = edit.nodes.new("GeometryNodeGroup")
                gn.node_tree = tree
                gn.location = space.cursor_location
                gn.select = True
                edit.nodes.active = gn
    except build.BuildError as e:
        set_status(s, "ERROR", f"Build failed: {e}")
        report({"ERROR"}, f"Build failed: {e}")
        return False

    msg = f"{verb} '{tree.name}' ({result.node_count} nodes)"
    if result.notes:
        set_status(s, "WARNING", msg + " — " + " | ".join(result.notes))
        report({"WARNING"}, " | ".join(result.notes))
    else:
        set_status(s, "INFO", msg)
        report({"INFO"}, msg)
    return True


# ═════════════════════════════════════════════════════════════════════════════
#  Properties
# ═════════════════════════════════════════════════════════════════════════════

class FORMULA_PG_ScriptLine(PropertyGroup):
    text: StringProperty(name="", default="")


def _ref_items(self, context):
    return [(c, c, "") for c in compiler.CATEGORY_ORDER]


class FORMULA_PG_Settings(PropertyGroup):
    formula: StringProperty(name="Formula", default='f@moss = max(dot(v@N, {0, 0, 1}), 0) ** chf("sharpness", 2)')
    group_name: StringProperty(name="Name", default="Formula")
    auto_add: BoolProperty(name="Add Node to Editor", default=True)
    output_type: EnumProperty(
        name="Output",
        items=[("AUTO", "Auto", "Geometry for assignments, a Result field for expressions"),
               ("GEOMETRY", "Geometry", "Require an attribute assignment"),
               ("FLOAT", "Float", "Force a float Result")],
        default="AUTO")
    replace_active: BoolProperty(
        name="Update Selected Group",
        description="If the active node is a Formula group, rebuild it in place — keeps its name, "
                    "links and modifier values. Hand-made groups are never touched",
        default=True)

    script_source: EnumProperty(
        name="Source",
        items=[("PANEL", "Panel", "Edit the script in this panel"),
               ("TEXT", "Text Editor", "Use a Text datablock")],
        default="PANEL")
    script_text: PointerProperty(type=bpy.types.Text, name="Script")
    script_lines: CollectionProperty(type=FORMULA_PG_ScriptLine)

    status: StringProperty(default="")
    status_level: EnumProperty(items=[("INFO", "", ""), ("WARNING", "", ""), ("ERROR", "", "")], default="INFO")
    error_line: IntProperty(default=-1)

    ai_prompt: StringProperty(
        name="Describe",
        description="Describe the effect you want in plain language",
        default="")
    ai_use_context: BoolProperty(
        name="Edit Current Script",
        description="Send the current script so the AI changes it instead of starting over",
        default=False)
    ai_auto_build: BoolProperty(name="Build When Done", default=True)
    ai_show_log: BoolProperty(name="Attempts", default=False)
    ai_log: StringProperty(default="")

    ref_category: EnumProperty(name="Category", items=_ref_items)


# ═════════════════════════════════════════════════════════════════════════════
#  Preferences
# ═════════════════════════════════════════════════════════════════════════════

class FORMULA_AP_Preferences(AddonPreferences):
    bl_idname = ADDON_ID

    provider: EnumProperty(
        name="Provider",
        items=[(k, v["label"], "") for k, v in ai.PROVIDERS.items()],
        default="ANTHROPIC")
    api_key: StringProperty(
        name="API Key", subtype="PASSWORD",
        description="Stored in your Blender preferences. Leave empty to use the "
                    "ANTHROPIC_API_KEY / OPENAI_API_KEY environment variable instead")
    model: StringProperty(name="Model", description="Leave empty for the provider default")
    endpoint: StringProperty(name="Endpoint URL", description="Leave empty for the provider default")
    timeout: IntProperty(name="Timeout (s)", default=90, min=10, max=600)
    max_attempts: IntProperty(name="Attempts", default=3, min=1, max=6,
                              description="How many times to ask the model to fix compile errors")

    def config(self):
        info = ai.PROVIDERS[self.provider]
        key = self.api_key or (os.environ.get(info["env"], "") if info["env"] else "")
        return ai.Config(self.provider, key, self.model, self.endpoint, self.timeout, self.max_attempts)

    def draw(self, context):
        layout = self.layout
        layout.label(text="AI Assist", icon="LIGHT")
        col = layout.column()
        col.prop(self, "provider")
        info = ai.PROVIDERS[self.provider]
        if info["needs_key"] or self.provider == "CUSTOM":
            col.prop(self, "api_key")
        row = col.row()
        row.prop(self, "model", placeholder=info["model"] or "required, e.g. the model you have pulled")
        row = col.row()
        row.prop(self, "endpoint", placeholder=info["url"] or "http://localhost:1234/v1/chat/completions")
        row = col.row(align=True)
        row.prop(self, "timeout")
        row.prop(self, "max_attempts")
        cfg = self.config()
        problem = cfg.problem()
        box = layout.box()
        if problem:
            box.label(text=problem, icon="ERROR")
        else:
            box.label(text=f"Ready: {cfg.label} · {cfg.model}", icon="CHECKMARK")
        if not cfg.is_local and not bpy.app.online_access:
            box.label(text="Online access is off (Preferences › System › Network)", icon="INFO")
        box.label(text="Your prompt and script are sent to the provider you choose.", icon="INFO")


# ═════════════════════════════════════════════════════════════════════════════
#  Operators — build
# ═════════════════════════════════════════════════════════════════════════════

class _GeoEditorOp:
    @classmethod
    def poll(cls, context):
        return geo_space(context) is not None and context.scene is not None


class FORMULA_OT_Generate(_GeoEditorOp, Operator):
    bl_idname = "node.formula_generate"
    bl_label = "Generate from Formula"
    bl_description = "Build (or update) a node group from the one-line formula"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = settings(context)
        ok = compile_and_build(context, geo_space(context), s.formula, "FORMULA", s.output_type, self.report)
        return {"FINISHED"} if ok else {"CANCELLED"}


class FORMULA_OT_GenerateScript(_GeoEditorOp, Operator):
    bl_idname = "node.formula_generate_script"
    bl_label = "Generate from Script"
    bl_description = "Build (or update) a node group from the script"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = settings(context)
        src = script_text(s)
        if not src.strip():
            set_status(s, "ERROR", "The script is empty — type a line, paste one, or pick an example")
            self.report({"ERROR"}, s.status)
            return {"CANCELLED"}
        ok = compile_and_build(context, geo_space(context), src, "SCRIPT", "AUTO", self.report)
        return {"FINISHED"} if ok else {"CANCELLED"}


class FORMULA_OT_Check(Operator):
    bl_idname = "node.formula_check"
    bl_label = "Check"
    bl_description = "Compile without building anything and report problems"

    target: EnumProperty(items=[("SCRIPT", "Script", ""), ("FORMULA", "Formula", "")], default="SCRIPT")

    def execute(self, context):
        s = settings(context)
        src = s.formula if self.target == "FORMULA" else script_text(s)
        try:
            res = compiler.compile_source(src, self.target, s.output_type if self.target == "FORMULA" else "AUTO")
        except FormulaError as e:
            set_status(s, "ERROR", str(e), e.line or -1)
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        msg = f"OK — {res.node_count} nodes, {sum(1 for i in res.iface if i.key.startswith('param:') and i.vtype != 'GEOMETRY')} parameters"
        if res.notes:
            set_status(s, "WARNING", msg + " — " + " | ".join(res.notes))
        else:
            set_status(s, "INFO", msg)
        self.report({"INFO"}, msg)
        return {"FINISHED"}


class FORMULA_OT_LoadActive(_GeoEditorOp, Operator):
    bl_idname = "node.formula_load_active"
    bl_label = "Edit Selected Group"
    bl_description = "Load the source of the selected Formula group back into the panel"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return active_formula_group(geo_space(context)) is not None

    def execute(self, context):
        s = settings(context)
        tree = active_formula_group(geo_space(context)).node_tree
        src = tree[build.SOURCE_KEY]
        if tree.get(build.MODE_KEY) == "FORMULA" and "\n" not in src:
            s.formula = src
            s.output_type = tree.get(build.OUTPUT_KEY, "AUTO")
        else:
            set_script_text(s, src)
        set_status(s, "INFO", f"Loaded '{tree.name}' — edit, then Generate to update it in place")
        return {"FINISHED"}


class FORMULA_OT_LoadExample(Operator):
    bl_idname = "node.formula_load_example"
    bl_label = "Load Example"
    bl_options = {"REGISTER", "UNDO"}

    key: StringProperty()

    @classmethod
    def description(cls, context, props):
        ex = examples.get(props.key)
        return ex[2] if ex else ""

    def execute(self, context):
        ex = examples.get(self.key)
        if not ex:
            return {"CANCELLED"}
        s = settings(context)
        set_script_text(s, ex[3])
        set_status(s, "INFO", f"Loaded example: {ex[1]}")
        return {"FINISHED"}


# ── script line editing ─────────────────────────────────────────────────────

class FORMULA_OT_ScriptAddLine(Operator):
    bl_idname = "node.formula_script_add_line"
    bl_label = "Add Line"
    bl_options = {"REGISTER", "UNDO"}
    index: IntProperty(default=-1)

    def execute(self, context):
        lines = settings(context).script_lines
        lines.add()
        if 0 <= self.index < len(lines) - 1:
            lines.move(len(lines) - 1, self.index + 1)
        return {"FINISHED"}


class FORMULA_OT_ScriptRemoveLine(Operator):
    bl_idname = "node.formula_script_remove_line"
    bl_label = "Remove Line"
    bl_options = {"REGISTER", "UNDO"}
    index: IntProperty(default=0)

    def execute(self, context):
        lines = settings(context).script_lines
        if 0 <= self.index < len(lines):
            lines.remove(self.index)
        return {"FINISHED"}


class FORMULA_OT_ScriptPaste(Operator):
    bl_idname = "node.formula_script_paste"
    bl_label = "Paste Script"
    bl_description = "Replace the script with text from the clipboard"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        clip = context.window_manager.clipboard
        if not clip.strip():
            self.report({"WARNING"}, "Clipboard is empty")
            return {"CANCELLED"}
        s = settings(context)
        set_script_text(s, ai.extract_script(clip) if "```" in clip else clip)
        return {"FINISHED"}


class FORMULA_OT_ScriptCopy(Operator):
    bl_idname = "node.formula_script_copy"
    bl_label = "Copy Script"
    bl_description = "Copy the whole script to the clipboard"

    def execute(self, context):
        context.window_manager.clipboard = script_text(settings(context))
        self.report({"INFO"}, "Script copied")
        return {"FINISHED"}


class FORMULA_OT_ScriptClear(Operator):
    bl_idname = "node.formula_script_clear"
    bl_label = "Clear Script"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = settings(context)
        set_script_text(s, "")
        set_status(s, "INFO", "")
        return {"FINISHED"}


# ═════════════════════════════════════════════════════════════════════════════
#  Operators — AI
# ═════════════════════════════════════════════════════════════════════════════

_job = None


def active_job():
    return _job if _job is not None and not _job.done else None


def _find_geo_area(context, pointer):
    screen = context.window.screen if context.window else None
    areas = [a for a in (screen.areas if screen else []) if a.type == "NODE_EDITOR"
             and a.spaces.active.tree_type == "GeometryNodeTree"]
    for a in areas:
        if a.as_pointer() == pointer:
            return a
    return areas[0] if areas else None


class FORMULA_OT_AIGenerate(_GeoEditorOp, Operator):
    bl_idname = "node.formula_ai_generate"
    bl_label = "Generate with AI"
    bl_description = ("Turn the description into a script. Each reply is compiled; "
                      "errors are sent back to the model to fix")
    bl_options = {"REGISTER", "UNDO"}

    _timer = None
    _area_ptr = 0

    @classmethod
    def poll(cls, context):
        return super().poll(context) and active_job() is None

    def invoke(self, context, event):
        global _job
        s = settings(context)
        p = prefs(context)
        if p is None:
            self.report({"ERROR"}, "Add-on preferences not found")
            return {"CANCELLED"}
        cfg = p.config()
        problem = cfg.problem()
        if problem:
            set_status(s, "ERROR", problem)
            self.report({"ERROR"}, problem)
            return {"CANCELLED"}
        if not cfg.is_local and not bpy.app.online_access:
            msg = "Online access is disabled — enable it in Preferences › System › Network, or use a local model"
            set_status(s, "ERROR", msg)
            self.report({"ERROR"}, msg)
            return {"CANCELLED"}
        current = script_text(s) if s.ai_use_context else None
        if not s.ai_prompt.strip() and not (current and current.strip()):
            set_status(s, "ERROR", "Describe what you want first")
            self.report({"ERROR"}, s.status)
            return {"CANCELLED"}
        current_error = None
        if current:
            ok, err, _ = compiler.check(current)
            current_error = None if ok else str(err)

        _job = ai.Job(cfg, s.ai_prompt, current, current_error)
        _job.start()
        s.ai_log = ""
        set_status(s, "INFO", _job.status)
        self._area_ptr = context.area.as_pointer() if context.area else 0
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.25, window=context.window)
        wm.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def _cleanup(self, context):
        if self._timer is not None:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None

    def modal(self, context, event):
        if event.type != "TIMER":
            return {"PASS_THROUGH"}
        job = _job
        s = settings(context)
        if job is None:
            self._cleanup(context)
            return {"CANCELLED"}
        if not job.done:
            if s.status != job.status:
                s.status = job.status
                tag_redraw_node_editors()
            return {"PASS_THROUGH"}

        self._cleanup(context)
        s.ai_log = "\n".join(f"Attempt {a}: {m}" for a, m in job.log)
        if job.cancelled:
            set_status(s, "WARNING", "AI request cancelled")
            tag_redraw_node_editors()
            return {"CANCELLED"}
        if not job.script:
            set_status(s, "ERROR", f"AI: {job.error}")
            self.report({"ERROR"}, s.status)
            tag_redraw_node_editors()
            return {"CANCELLED"}

        set_script_text(s, job.script)
        if job.error:
            set_status(s, "ERROR", f"AI script still has an error after {job.attempt} attempts — {job.error}",
                       job.error_line or -1)
            self.report({"WARNING"}, s.status)
        elif s.ai_auto_build:
            area = _find_geo_area(context, self._area_ptr)
            space = area.spaces.active if area else None
            compile_and_build(context, space, job.script, "SCRIPT", "AUTO", self.report)
            s.status = f"AI ({job.attempt} attempt{'s' if job.attempt > 1 else ''}): " + s.status
        else:
            set_status(s, "INFO", f"AI script ready after {job.attempt} attempt(s) — press Generate")
        tag_redraw_node_editors()
        return {"FINISHED"}

    def cancel(self, context):
        self._cleanup(context)
        if _job is not None:
            _job.cancel()


class FORMULA_OT_AICancel(Operator):
    bl_idname = "node.formula_ai_cancel"
    bl_label = "Cancel"
    bl_description = "Stop waiting for the AI reply"

    @classmethod
    def poll(cls, context):
        return active_job() is not None

    def execute(self, context):
        active_job().cancel()
        return {"FINISHED"}


class FORMULA_OT_AICopyPrompt(Operator):
    bl_idname = "node.formula_ai_copy_prompt"
    bl_label = "Copy Prompt for Any Chatbot"
    bl_description = ("Copy the full language guide plus your request, to paste into any AI chat. "
                      "Paste the reply back with the Paste button — no API key needed")

    def execute(self, context):
        s = settings(context)
        current = script_text(s) if s.ai_use_context else None
        text = (ai.build_system_prompt() + "\n\n---\n\n"
                + ai.build_user_message(s.ai_prompt or "(describe your effect here)", current))
        context.window_manager.clipboard = text
        self.report({"INFO"}, f"Copied {len(text)} characters")
        return {"FINISHED"}


class FORMULA_OT_OpenPrefs(Operator):
    bl_idname = "node.formula_open_prefs"
    bl_label = "AI Settings"
    bl_description = "Open the add-on preferences"

    def execute(self, context):
        bpy.ops.screen.userpref_show("INVOKE_DEFAULT")
        context.preferences.active_section = "ADDONS"
        bpy.ops.preferences.addon_show(module=ADDON_ID)
        return {"FINISHED"}


# ═════════════════════════════════════════════════════════════════════════════
#  Menus & panels
# ═════════════════════════════════════════════════════════════════════════════

class FORMULA_MT_Examples(Menu):
    bl_idname = "FORMULA_MT_examples"
    bl_label = "Examples"

    def draw(self, context):
        for key, title, desc, src in examples.EXAMPLES:
            self.layout.operator("node.formula_load_example", text=title).key = key


class _PanelBase:
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Formula"

    @classmethod
    def poll(cls, context):
        return geo_space(context) is not None


class FORMULA_PT_Panel(_PanelBase, Panel):
    bl_label = "Formula to Nodes"
    bl_idname = "FORMULA_PT_panel"

    def draw_header_preset(self, context):
        self.layout.label(text=f"v{VERSION}")

    def draw(self, context):
        layout = self.layout
        s = settings(context)

        if s.status:
            box = layout.box()
            icon = {"INFO": "CHECKMARK", "WARNING": "ERROR", "ERROR": "CANCEL"}[s.status_level]
            col = box.column(align=True)
            col.alert = s.status_level == "ERROR"
            _wrap(col, s.status, icon, width=_chars(context))

        node = active_formula_group(geo_space(context))
        if node:
            row = layout.row(align=True)
            row.label(text=f"Selected: {node.node_tree.name}", icon="NODETREE")
            row.operator("node.formula_load_active", text="Edit", icon="GREASEPENCIL")

        box = layout.box()
        box.label(text="Quick Formula", icon="SYNTAX_ON")
        box.prop(s, "formula", text="")
        row = box.row(align=True)
        row.prop(s, "output_type", text="")
        row.operator("node.formula_check", text="", icon="VIEWZOOM").target = "FORMULA"
        box.operator("node.formula_generate", icon="NODETREE")

        col = layout.column(align=True)
        col.prop(s, "group_name")
        col.prop(s, "auto_add")
        col.prop(s, "replace_active")


class FORMULA_PT_Script(_PanelBase, Panel):
    bl_label = "Script"
    bl_idname = "FORMULA_PT_script"
    bl_parent_id = "FORMULA_PT_panel"

    def draw_header_preset(self, context):
        self.layout.menu("FORMULA_MT_examples", text="Examples", icon="PRESET")

    def draw(self, context):
        layout = self.layout
        s = settings(context)
        layout.row().prop(s, "script_source", expand=True)
        if s.script_source == "TEXT":
            layout.template_ID(s, "script_text", new="text.new", open="text.open")
        else:
            row = layout.row(align=True)
            row.operator("node.formula_script_paste", text="Paste", icon="PASTEDOWN")
            row.operator("node.formula_script_copy", text="Copy", icon="COPYDOWN")
            row.operator("node.formula_script_clear", text="", icon="TRASH")
            col = layout.column(align=True)
            for i, line in enumerate(s.script_lines):
                r = col.row(align=True)
                r.alert = (i + 1) == s.error_line
                r.label(text=f"{i + 1:>2}")
                r.prop(line, "text", text="")
                r.operator("node.formula_script_add_line", text="", icon="ADD").index = i
                r.operator("node.formula_script_remove_line", text="", icon="X").index = i
            col.operator("node.formula_script_add_line", text="Add Line", icon="ADD").index = -1
        row = layout.row(align=True)
        row.scale_y = 1.3
        row.operator("node.formula_check", text="", icon="VIEWZOOM").target = "SCRIPT"
        row.operator("node.formula_generate_script", icon="NODETREE")


class FORMULA_PT_AI(_PanelBase, Panel):
    bl_label = "AI Assist"
    bl_idname = "FORMULA_PT_ai"
    bl_parent_id = "FORMULA_PT_panel"

    def draw_header(self, context):
        self.layout.label(icon="LIGHT")

    def draw_header_preset(self, context):
        self.layout.operator("node.formula_open_prefs", text="", icon="PREFERENCES", emboss=False)

    def draw(self, context):
        layout = self.layout
        s = settings(context)
        p = prefs(context)
        layout.prop(s, "ai_prompt", text="", placeholder="e.g. bumpy noise that grows upward over time")
        row = layout.row(align=True)
        row.prop(s, "ai_use_context", toggle=True, icon="TEXT")
        row.prop(s, "ai_auto_build", toggle=True, icon="NODETREE")

        job = active_job()
        if job:
            row = layout.row(align=True)
            row.label(text=job.status, icon="SORTTIME")
            row.operator("node.formula_ai_cancel", text="", icon="X")
        else:
            row = layout.row()
            row.scale_y = 1.3
            row.operator("node.formula_ai_generate", icon="LIGHT")

        if p is not None:
            cfg = p.config()
            problem = cfg.problem()
            sub = layout.column(align=True)
            sub.scale_y = 0.8
            if problem:
                sub.label(text=problem, icon="INFO")
            else:
                sub.label(text=f"{cfg.label} · {cfg.model}")
        layout.operator("node.formula_ai_copy_prompt", icon="COPYDOWN")
        if s.ai_log:
            box = layout.box()
            box.prop(s, "ai_show_log", icon="TRIA_DOWN" if s.ai_show_log else "TRIA_RIGHT", emboss=False)
            if s.ai_show_log:
                col = box.column(align=True)
                col.scale_y = 0.8
                for entry in s.ai_log.split("\n"):
                    _wrap(col, entry, "BLANK1", width=_chars(context))


SYNTAX_LINES = [
    "f@ float  v@ vector  i@ int  b@ bool  (@name = vector)",
    "@P @N @ptnum @id @pscale @Time @Frame @numpt",
    "float a = 1;  vector v = {0, 0, 1};  v.z  v[2]",
    "if (a > 0) { ... } else { ... }    a > 0 ? 1 : 2",
    "&& || !   == != < <= > >=   + - * / % **",
    "simulate { deltatime }   repeat(n) { iteration }",
    "foreach(face) { elemindex }     return expr;",
    'chf("amp", 0.5, min=0, max=1)  chi  chv  chb',
    "Bare names like amp become float sliders (default 1)",
]


class FORMULA_PT_Reference(_PanelBase, Panel):
    bl_label = "Reference"
    bl_idname = "FORMULA_PT_reference"
    bl_parent_id = "FORMULA_PT_panel"
    bl_options = {"DEFAULT_CLOSED"}


    def draw(self, context):
        layout = self.layout
        s = settings(context)
        col = layout.column(align=True)
        col.scale_y = 0.8
        for line in SYNTAX_LINES:
            col.label(text=line)
        layout.prop(s, "ref_category", text="")
        col = layout.column(align=True)
        col.scale_y = 0.8
        for fd in compiler.reference_by_category().get(s.ref_category, []):
            col.label(text=fd.sig)
            sub = col.row()
            sub.enabled = False
            sub.label(text="    " + fd.doc)


# ═════════════════════════════════════════════════════════════════════════════
#  Claude Desktop / MCP
# ═════════════════════════════════════════════════════════════════════════════

def mcp_bridge_state(context):
    """(addon_module or None, host, port, running) for Blender Lab's MCP add-on."""
    for key in context.preferences.addons.keys():
        if key == "mcp" or key.endswith(".mcp"):
            p = context.preferences.addons[key].preferences
            host = getattr(p, "host", "localhost")
            port = getattr(p, "port", 9876)
            server = sys.modules.get(key + ".mcp_to_blender_server")
            running = bool(server and server.is_running())
            return key, host, port, running
    return None, "localhost", 9876, False


def _mcp_entry(context):
    _key, host, port, _running = mcp_bridge_state(context)
    return mcp_setup.server_entry(host, port)


class FORMULA_OT_MCPConnectClaude(Operator):
    bl_idname = "node.formula_mcp_connect_claude"
    bl_label = "Connect Claude Desktop"
    bl_description = ("Add Formula to Nodes to Claude Desktop's MCP config (a backup .bak is kept). "
                      "Restart Claude Desktop afterwards")

    def execute(self, context):
        entry = _mcp_entry(context)
        done, failed = [], []
        for path in mcp_setup.claude_desktop_config_paths():
            try:
                done.append(f"{mcp_setup.register(path, entry)} {path}")
            except (OSError, ValueError) as e:
                failed.append(f"{path}: {e}")
        s = settings(context)
        if failed and not done:
            set_status(s, "ERROR", "Couldn't write the Claude config — " + "; ".join(failed))
            self.report({"ERROR"}, s.status)
            return {"CANCELLED"}
        msg = "Claude Desktop connected — quit and reopen Claude to load the tools"
        set_status(s, "WARNING" if failed else "INFO", msg + (" (some files failed: " + "; ".join(failed) + ")" if failed else ""))
        self.report({"INFO"}, msg + " | " + " | ".join(done))
        return {"FINISHED"}


class FORMULA_OT_MCPDisconnectClaude(Operator):
    bl_idname = "node.formula_mcp_disconnect_claude"
    bl_label = "Remove from Claude Desktop"
    bl_description = "Remove Formula to Nodes from Claude Desktop's MCP config"

    def execute(self, context):
        removed = 0
        for path in mcp_setup.claude_desktop_config_paths():
            try:
                removed += bool(mcp_setup.unregister(path))
            except (OSError, ValueError):
                pass
        set_status(settings(context), "INFO", "Removed from Claude Desktop" if removed else "Wasn't registered")
        return {"FINISHED"}


class FORMULA_OT_MCPCopy(Operator):
    bl_idname = "node.formula_mcp_copy"
    bl_label = "Copy MCP Config"
    bl_description = "Copy the MCP server config for other clients"

    kind: EnumProperty(items=[("JSON", "JSON", "mcpServers JSON block"),
                              ("CLAUDE_CODE", "Claude Code", "claude mcp add command")], default="JSON")

    def execute(self, context):
        entry = _mcp_entry(context)
        text = mcp_setup.config_snippet(entry) if self.kind == "JSON" else mcp_setup.claude_code_command(entry)
        context.window_manager.clipboard = text
        self.report({"INFO"}, "Copied")
        return {"FINISHED"}


class FORMULA_OT_MCPStartBridge(Operator):
    bl_idname = "node.formula_mcp_start_bridge"
    bl_label = "Start Bridge"
    bl_description = "Start the MCP add-on's bridge server so Claude can reach this Blender"

    @classmethod
    def poll(cls, context):
        key, _h, _p, running = mcp_bridge_state(context)
        return key is not None and not running

    def execute(self, context):
        try:
            bpy.ops.blmcp.server_start()
        except RuntimeError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        return {"FINISHED"}


class FORMULA_PT_MCP(_PanelBase, Panel):
    bl_label = "Claude Desktop (MCP)"
    bl_idname = "FORMULA_PT_mcp"
    bl_parent_id = "FORMULA_PT_panel"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        key, host, port, running = mcp_bridge_state(context)
        col = layout.column(align=True)
        if key is None:
            col.label(text="Install/enable the 'MCP' add-on", icon="ERROR")
            col.label(text="(Blender Lab) — it's the bridge Claude uses", icon="BLANK1")
        elif not bpy.app.online_access:
            col.label(text="Turn on Allow Online Access", icon="ERROR")
            col.label(text="Preferences › System › Network", icon="BLANK1")
        elif running:
            col.label(text=f"Bridge running on {host}:{port}", icon="CHECKMARK")
        else:
            row = col.row()
            row.label(text="Bridge stopped", icon="X")
            row.operator("node.formula_mcp_start_bridge", text="Start", icon="PLAY")

        registered = any(mcp_setup.is_registered(p) for p in mcp_setup.claude_desktop_config_paths())
        if registered:
            layout.label(text="Registered in Claude Desktop", icon="CHECKMARK")
        row = layout.row()
        row.scale_y = 1.3
        row.operator("node.formula_mcp_connect_claude",
                     text="Reconnect Claude Desktop" if registered else "Connect Claude Desktop", icon="LINKED")
        row = layout.row(align=True)
        row.operator("node.formula_mcp_copy", text="Copy JSON", icon="COPYDOWN").kind = "JSON"
        row.operator("node.formula_mcp_copy", text="Claude Code", icon="CONSOLE").kind = "CLAUDE_CODE"
        if registered:
            layout.operator("node.formula_mcp_disconnect_claude", icon="UNLINKED")


def _chars(context):
    width = context.region.width if context.region else 300
    return max(20, int(width / (7.0 * max(context.preferences.system.ui_scale, 0.5))))


def _wrap(layout, text, icon, width=40):
    words, line, first = text.split(), "", True
    for w in words:
        if len(line) + len(w) + 1 > width and line:
            layout.label(text=line, icon=icon if first else "BLANK1")
            line, first = w, False
        else:
            line = f"{line} {w}".strip()
    if line or first:
        layout.label(text=line, icon=icon if first else "BLANK1")


classes = (
    FORMULA_PG_ScriptLine,
    FORMULA_PG_Settings,
    FORMULA_AP_Preferences,
    FORMULA_OT_Generate,
    FORMULA_OT_GenerateScript,
    FORMULA_OT_Check,
    FORMULA_OT_LoadActive,
    FORMULA_OT_LoadExample,
    FORMULA_OT_ScriptAddLine,
    FORMULA_OT_ScriptRemoveLine,
    FORMULA_OT_ScriptPaste,
    FORMULA_OT_ScriptCopy,
    FORMULA_OT_ScriptClear,
    FORMULA_OT_AIGenerate,
    FORMULA_OT_AICancel,
    FORMULA_OT_AICopyPrompt,
    FORMULA_OT_OpenPrefs,
    FORMULA_MT_Examples,
    FORMULA_PT_Panel,
    FORMULA_PT_Script,
    FORMULA_PT_AI,
    FORMULA_OT_MCPConnectClaude,
    FORMULA_OT_MCPDisconnectClaude,
    FORMULA_OT_MCPCopy,
    FORMULA_OT_MCPStartBridge,
    FORMULA_PT_MCP,
    FORMULA_PT_Reference,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.formula_settings = PointerProperty(type=FORMULA_PG_Settings)


def unregister():
    if _job is not None:
        _job.cancel()
    if hasattr(bpy.types.Scene, "formula_settings"):
        del bpy.types.Scene.formula_settings
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
