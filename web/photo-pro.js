import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const base = "/famegrid/photo-pro";
const style = document.createElement("style");
style.textContent = `.fg-photo-node{height:100%;display:flex;flex-direction:column;background:radial-gradient(ellipse at top,#33244c,#15151d 70%);border:1px solid #685185;border-radius:14px;padding:12px;color:#eee8ff;font-family:system-ui,sans-serif;box-sizing:border-box;overflow:hidden}.fg-photo-brand{display:flex;align-items:center;justify-content:space-between;font-size:11px;letter-spacing:.14em;font-weight:800;padding:2px 0 10px}.fg-photo-brand span{color:#b99aee;font-size:10px;letter-spacing:.03em}.fg-photo-frame{flex:1;min-height:120px;position:relative;display:flex;align-items:center;justify-content:center;border:1px solid #44354e;border-radius:10px;background:#0b0b11;overflow:hidden;cursor:pointer}.fg-photo-frame img{width:100%;height:100%;position:absolute;object-fit:contain}.fg-photo-frame p{font-size:12px;line-height:1.7;text-align:center;padding:15px;color:#aea3bf}.fg-photo-node button{margin-top:10px;width:100%;min-height:38px;border:1px solid #b295ee;border-radius:9px;background:linear-gradient(110deg,#7148c9,#994fcd);color:white;font-size:12px;font-weight:700;cursor:pointer}.fg-photo-status{padding-top:8px;font-size:10px;color:#ada3bd;white-space:nowrap;text-overflow:ellipsis;overflow:hidden}.fg-photo-modal{position:fixed;inset:0;z-index:100000;background:#000b;display:flex;align-items:center;justify-content:center;padding:12px}.fg-photo-dialog{width:min(1500px,100%);height:calc(100dvh - 24px);background:#101017;border:1px solid #77628c;border-radius:16px;overflow:hidden;box-shadow:0 25px 100px #000b}.fg-photo-dialog iframe{display:block;width:100%;height:100%;border:0}`;
document.head.append(style);

const widget = (node, name) => node.widgets?.find(w => w.name === name);
const value = (node, name) => widget(node, name)?.value;
function openEditor(node) {
  if (document.querySelector(".fg-photo-modal")) return;
  const overlay = document.createElement("div"), dialog = document.createElement("div"), frame = document.createElement("iframe");
  overlay.className = "fg-photo-modal"; overlay.setAttribute("role", "dialog"); overlay.setAttribute("aria-modal", "true"); overlay.setAttribute("aria-label", "FameGrid Photo Pro editor"); dialog.className = "fg-photo-dialog"; frame.title = "FameGrid Photo Pro live editor";
  frame.src = api.apiURL(`${base}/editor/index.html`);
  dialog.append(frame); overlay.append(dialog); document.body.append(overlay);
  const previous = document.activeElement;
  const close = () => { window.removeEventListener("message", receive); document.removeEventListener("keydown", keydown); overlay.remove(); previous?.focus?.(); };
  const keydown = event => { if (event.key === "Escape") close(); };
  const receive = async event => {
    if (event.source !== frame.contentWindow || event.origin !== location.origin || !event.data?.famegridPhotoPro) return;
    if (event.data.type === "ready") {
      const available = await api.fetchApi(`${base}/last/${encodeURIComponent(node.id)}`).then(r => r.json()).catch(() => ({ available: false }));
      let settings = {}; try { settings = JSON.parse(value(node, "settings_json") || "{}"); } catch {}
      frame.contentWindow?.postMessage({ famegridPhotoPro: true, type: "init", nodeId: String(node.id), apiBase: api.apiURL(base), settings, sourceMode: value(node, "source_mode"), inputFile: value(node, "input_file") || "", lastAvailable: available.available === true }, location.origin);
    } else if (event.data.type === "apply") {
      widget(node, "settings_json").value = JSON.stringify(event.data.settings);
      widget(node, "source_mode").value = event.data.sourceMode;
      widget(node, "input_file").value = event.data.inputFile || "";
      app.graph?.setDirtyCanvas(true, true); node.fgRefresh?.(); close();
    } else if (event.data.type === "cancel") close();
  };
  window.addEventListener("message", receive); document.addEventListener("keydown", keydown); frame.focus();
}

app.registerExtension({ name: "FameGrid.PhotoPro",
  async beforeRegisterNodeDef(nodeType, data) {
    if (data.name !== "FameGridPhotoPro") return;
    const created = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const result = created?.apply(this, arguments); this.color = "#44305e"; this.bgcolor = "#211b2e";
      for (const name of ["source_mode", "input_file", "settings_json"]) {
        const w = widget(this, name); if (!w) continue;
        w.type = "hidden"; w.computeSize = () => [0, -4]; w.hidden = true;
        if (w.inputEl) w.inputEl.style.display = "none";
        if (w.element) w.element.style.display = "none";
      }
      const root = document.createElement("div"); root.className = "fg-photo-node";
      root.innerHTML = `<div class="fg-photo-brand">FAMEGRID PHOTO PRO <span>FLOAT32</span></div><div class="fg-photo-frame"><p>Adjust settings before running,<br>or connect an IMAGE and run once<br>to preview the incoming photo.</p><img hidden alt="FameGrid developed image"></div><button type="button">Adjust Settings/Edit Photo</button><div class="fg-photo-status">Color · light · detail · grain · crop</div>`;
      const button = root.querySelector("button"), image = root.querySelector("img"), empty = root.querySelector("p"), status = root.querySelector(".fg-photo-status");
      const refresh = () => { status.textContent = value(this, "source_mode") === "uploaded image" ? "Uploaded photo · settings saved in workflow" : "Incoming image · settings apply on each run"; image.src = api.apiURL(`${base}/thumbnail/${encodeURIComponent(this.id)}?v=${Date.now()}`); };
      image.onload = () => { image.hidden = false; empty.hidden = true; }; image.onerror = () => { image.hidden = true; empty.hidden = false; };
      button.onclick = () => openEditor(this); root.querySelector(".fg-photo-frame").onclick = button.onclick;
      this.fgRefresh = refresh;
      const dom = this.addDOMWidget("famegrid_preview", "famegrid_preview", root, { serialize: false, hideOnZoom: false });
      dom.computeSize = () => [420, 280]; this.setSize([440, 350]);
      const configured = this.onConfigure; this.onConfigure = function () { configured?.apply(this, arguments); refresh(); };
      const removed = this.onRemoved; this.onRemoved = function () { this.fgRefresh = null; root.remove(); removed?.apply(this, arguments); };
      setTimeout(refresh, 0); return result;
    };
  }
});
api.addEventListener("famegrid.photo-pro.updated", event => app.graph?.getNodeById(event.detail.node_id)?.fgRefresh?.());
