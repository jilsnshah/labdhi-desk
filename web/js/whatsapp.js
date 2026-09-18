// Send a sauda to the party on WhatsApp.
//
// The deal says who it is with (party_id); the party record holds the phone.
// Nothing is copied onto the deal - the number is read from the party when the
// message is sent, and a number typed here is saved to the party itself.
//
// On a phone WhatsApp is opened through its own URL scheme (whatsapp://send),
// which hands straight to the app. On iOS that is the only reliable route: the
// wa.me web link, opened from script, lands on a web page instead of the app.
// Safari may ask "Open in WhatsApp?" first, and a browser can refuse to open an
// app without a tap, so the card with a Send button always stays on screen.
import { h, toast } from './ui.js';
import { api } from './api.js';

const isPhone = () => /iPhone|iPad|iPod|Android/i.test(navigator.userAgent)
  || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);

// "+91 98250 00000 / 079 2656 1234" -> "919825000000". The first number on
// the line is taken; a 10-digit Indian mobile gets the 91 country code.
export function waPhone(raw) {
  const first = String(raw || '').split(/[\/,;|]| or /i).map(s => s.replace(/\D/g, '')).find(d => d.length >= 10);
  if (!first) return null;
  let d = first.replace(/^0+/, '');
  if (d.length === 10) d = '91' + d;
  return d.length >= 11 && d.length <= 15 ? d : null;
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
// 2026-09-17 -> 17-Sep-2026
const dmy = iso => {
  if (!iso) return '';
  const [y, m, d] = iso.slice(0, 10).split('-');
  return `${d}-${MONTHS[+m - 1]}-${y}`;
};
const dash = v => (v === null || v === undefined || String(v).trim() === '' ? '—' : String(v).trim());
const byWhom = (verb, who) => (who ? `${verb} by ${String(who).toLowerCase()}` : '');
const RULE = '──────────────';

// The sauda confirmation as Labdhi Exim sends it. Every field keeps its line;
// anything not recorded shows "—". Fields the deal carries beyond the standard
// layout (e-way bill, warehouse, payment due date, note) are kept, not dropped.
export function saudaMessage(deal, company, { revised = false } = {}) {
  const sell = deal.side === 'sell';
  const firm = String(company || 'Labdhi Exim').toUpperCase();
  const kg = Math.round(deal.qty_g / 1000);
  const mt = (deal.qty_g / 1e6).toLocaleString('en-IN', { maximumFractionDigits: 3 });
  const rate = `₹${(deal.rate_paise / 100).toFixed(2)}/kg ${deal.plus_gst ? '+ GST' : 'incl. GST'}`;
  const transport = [byWhom('Arranged', deal.delivery_by), deal.transporter_name].filter(Boolean).join(' · ');
  const product = deal.manufacturer ? `${deal.material} (${deal.manufacturer})` : deal.material;
  return [
    `🏢 *${firm}*`,
    revised ? '*REVISED SAUDA CONFIRMATION*' : '*SAUDA CONFIRMATION*',
    RULE,
    `📅 Date: ${dash(dmy(deal.deal_date))}`,
    `🔖 Sauda No.: ${dash(deal.ref)}`,
    `Seller: ${dash(sell ? firm : deal.party_name)}`,
    `Buyer: ${dash(sell ? deal.party_name : firm)}`,
    `Product: ${dash(product)}`,
    `Grade: ${dash(deal.grade)}`,
    `Quantity: ${deal.qty_g ? `${mt} MT (${kg.toLocaleString('en-IN')} kg)` : '—'}`,
    `Rate: ${deal.rate_paise ? rate : '—'}`,
    `Ex-Place: ${dash(deal.ex_place)}`,
    `Transport: ${dash(transport)}`,
    `Freight: ${dash(byWhom('Paid', deal.freight_by))}`,
    `Payment Terms: ${dash(deal.payment_terms)}`,
    `Payment Due: ${dash(dmy(deal.payment_due))}`,
    `E-way Bill: ${dash(deal.eway)}`,
    `${sell ? 'Dispatch From' : 'Delivery At'}: ${dash(deal.warehouse)}`,
    `Note: ${dash(deal.remarks)}`,
    RULE,
    '⚠️ Payment must be made strictly as per the agreed terms above. Delayed payment will attract interest @ 2% per month on the overdue amount until the date of actual payment.',
    RULE,
    'Kindly verify the above details and reply *CONFIRMED* to confirm this Sauda.',
    '',
    `*${firm}*`
  ].join('\n');
}

export function openWhatsApp(phone, text) {
  const q = `text=${encodeURIComponent(text)}`;
  if (isPhone()) {
    window.location.href = phone ? `whatsapp://send?phone=${phone}&${q}` : `whatsapp://send?${q}`;
  } else {
    window.open(phone ? `https://wa.me/${phone}?${q}` : `https://wa.me/?${q}`, '_blank', 'noopener');
  }
}

// The card shown after booking (auto = try to open WhatsApp straight away),
// and from any deal in the tape.
export async function sendSauda(deal, company, { auto = false, booked = auto, revised = false } = {}) {
  let party = null;
  try { party = await api.party(deal.party_id); } catch (_) { /* send without a number */ }
  const text = saudaMessage(deal, company || 'Labdhi Exim', { revised });
  let phone = party ? waPhone(party.phone) : null;

  const numberInput = h('input', {
    class: 'fld-input', type: 'tel', inputmode: 'tel', placeholder: '98250 00000',
    value: party && party.phone ? party.phone : ''
  });
  const numberRow = h('div', { class: 'fld', hidden: !!phone },
    h('label', { class: 'fld-label' }, `WhatsApp number for ${deal.party_name}`),
    numberInput,
    h('div', { class: 'fld-hint' }, 'Saved to the party, so next time it opens straight away.'));
  const sub = h('div', { class: 'modal-sub' }, '');
  const paintSub = () => {
    sub.textContent = phone ? `to ${deal.party_name} · +${phone}` : `${deal.party_name} has no WhatsApp number saved`;
  };
  paintSub();

  async function send() {
    if (!phone) {
      const typed = waPhone(numberInput.value);
      if (!typed) { toast('Enter a 10-digit mobile number', { kind: 'err' }); numberInput.focus(); return; }
      phone = typed;
      if (party) {
        try {
          await api.partySave({ id: party.id, name: party.name, phone: numberInput.value.trim(),
                                address: party.address || '', gstin: party.gstin || '',
                                pan: party.pan || '', state_code: party.state_code || '' });
        } catch (err) { toast(`Number not saved to party: ${err.message}`, { kind: 'err', ms: 8000 }); }
      }
      numberRow.hidden = true;
      paintSub();
    }
    openWhatsApp(phone, text);
  }

  async function copy() {
    try { await navigator.clipboard.writeText(text); toast('Sauda message copied'); }
    catch (_) {
      const area = h('textarea', {}); area.value = text; document.body.appendChild(area);
      area.select(); document.execCommand('copy'); area.remove(); toast('Sauda message copied');
    }
  }

  const close = () => { overlay.remove(); document.removeEventListener('keydown', onKey); };
  const onKey = e => { if (e.key === 'Escape') close(); };
  const card = h('div', { class: 'modal-card wa-card' },
    h('div', { class: 'modal-head' },
      h('div', {}, h('div', { class: 'modal-title' }, revised ? `${deal.ref} revised` : booked ? `${deal.ref} booked` : `Send ${deal.ref}`), sub),
      h('button', { type: 'button', class: 'modal-x', onclick: close }, '×')),
    h('pre', { class: 'wa-msg' }, text),
    numberRow,
    h('div', { class: 'wa-actions' },
      h('button', { type: 'button', class: 'wa-send', onclick: send }, 'Send on WhatsApp'),
      h('button', { type: 'button', class: 'form-cancel', onclick: copy }, 'Copy'),
      h('button', { type: 'button', class: 'form-cancel', onclick: close }, 'Done')));
  const overlay = h('div', { class: 'modal', onmousedown: e => { if (e.target === overlay) close(); } }, card);
  document.addEventListener('keydown', onKey);
  document.body.appendChild(overlay);

  if (auto && phone && isPhone()) openWhatsApp(phone, text);
}

