// An "Export to Excel" button. The report always covers exactly what the
// screen is showing - the caller hands over the screen's current filters at
// the moment of the tap.
import { h, toast } from './ui.js';
import { downloadFile } from './api.js';

export function exportButton(path, params, { phone = false, name = 'Labdhi-report.xlsx' } = {}) {
  const btn = h('button', {
    class: phone ? 'mexport' : 'export-btn',
    onclick: async () => {
      btn.disabled = true; const label = btn.textContent; btn.textContent = 'Preparing…';
      try {
        const how = await downloadFile(path, params(), name);
        if (how === 'downloaded') toast('Excel report downloaded');
      } catch (err) { toast(err.message, { kind: 'err', ms: 8000 }); }
      finally { btn.disabled = false; btn.textContent = label; }
    }
  }, '⬇ Export to Excel');
  return btn;
}
