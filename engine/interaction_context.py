"""DOM evidence about obstructions, not a replacement for actionability checks."""

CONTEXT_JS = r"""() => {
    const visible = el => {
        const r = el.getBoundingClientRect(), s = getComputedStyle(el);
        return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    };
    const dialogs = [...document.querySelectorAll('dialog[open], [role="dialog"], [role="alertdialog"], [aria-modal="true"]')].filter(visible);
    // Bounded non-editable text evidence: empty states and status messages are
    // often absent from the interactive-control inventory. Never read values.
    const mains = [...document.querySelectorAll('main,[role="main"]')].filter(visible);
    const root = dialogs.at(-1) || mains[0] || document.body;
    const scope = dialogs.length ? 'dialog' : mains.length ? 'main' : 'body';
    const excluded = 'script,style,noscript,template,input,textarea,select,option,[contenteditable]:not([contenteditable="false"]),[hidden],[aria-hidden="true"],[inert]';
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    let text = '', visited = 0, truncated = false, node;
    while ((node = walker.nextNode())) {
        if (++visited > 2000) { truncated = true; break; }
        const el = node.parentElement;
        if (!el || el.closest(excluded) || !visible(el)) continue;
        if (el.checkVisibility && !el.checkVisibility({opacityProperty:true, visibilityProperty:true})) continue;
        const chunk = (node.textContent || '').replace(/\s+/g, ' ').trim();
        if (!chunk) continue;
        const addition = (text ? '\n' : '') + chunk;
        if (text.length + addition.length > 2000) {
            text += addition.slice(0, 2000 - text.length);
            truncated = true;
            break;
        }
        text += addition;
    }
    const nodes = {}, registry = window.__qamateNodes;
    if (registry) {
        for (const el of document.querySelectorAll('input,textarea,select,button,a,[role],[tabindex]')) {
            const id = registry.ids.get(el);
            if (id === undefined || !visible(el)) continue;
            const r = el.getBoundingClientRect(), x = r.x + r.width/2, y = r.y + r.height/2;
            const inViewport = x >= 0 && y >= 0 && x < innerWidth && y < innerHeight;
            const hit = inViewport ? document.elementFromPoint(x, y) : null;
            nodes[registry.document + ':' + id] = {
                center_receives_pointer: inViewport ? !!hit && (hit === el || el.contains(hit)) : null,
                in_viewport: inViewport,
                inert: !!el.closest('[inert]')
            };
        }
    }
    return {visible_dialog_count: dialogs.length,
        visible_modal_count: dialogs.filter(el => el.getAttribute('aria-modal') === 'true' || el.matches(':modal')).length,
        nodes, rendered_text: {scope, text, truncated}};
}"""


def attach_interaction_context(page, observation, by_ref):
    """Probe failures are unknown, never evidence that the page is unobstructed."""
    try:
        context = page.evaluate(CONTEXT_JS)
        nodes = context.pop("nodes")
        if "rendered_text" in context:
            observation["rendered_text"] = context.pop("rendered_text")
        for element in observation.get("elements", []):
            node = by_ref.get(element.get("ref"), {}).get("node_id")
            if node in nodes:
                element.update(nodes[node])
        observation["interaction_context"] = context
        if observation.get("validation_errors"):
            observation["validation_guidance"] = (
                "Validation text alone does not establish a blocking overlay. For an expected negative-test "
                "outcome, assert it and continue the authorized form flow. Do not dismiss errors merely "
                "because they exist. Use visible modal and target obstruction evidence; unknown is not clear.")
    except Exception:
        observation["interaction_context"] = {"status": "unknown"}
    return observation


def decision_page(observation):
    return {key: observation[key] for key in ("url", "elements", "validation_errors",
        "interaction_context", "validation_guidance", "open_dropdown_options") if key in observation}
