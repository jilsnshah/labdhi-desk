// Send a sauda on WhatsApp: the confirmation for the party, and the firm's own
// copy with cost and margin in it. Both messages are written by the trader in
// Setup > Messages; this file only fills them in and hands them over.
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
import { build } from './message.js';

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

export function openWhatsApp(phone, text) {
  const q = `text=${encodeURIComponent(text)}`;
  if (isPhone()) {
    window.location.href = phone ? `whatsapp://send?phone=${phone}&${q}` : `whatsapp://send?${q}`;
  } else {
    window.open(phone ? `https://wa.me/${phone}?${q}` : `https://wa.me/?${q}`, '_blank', 'noopener');
  }
}

// The card shown after booking (auto = try to open WhatsApp straight away),
// and from any deal in the tape. It holds both messages: the party's, which
// goes to their number, and the internal one, which is copied and pasted into
// whichever group the firm uses - WhatsApp cannot open a group from a link.
export async function sendSauda(deal, settings, { auto = false, booked = auto, revised = false } = {}) {
  settings = typeof settings === 'string' ? { company_name: settings } : (settings || {});
  let party = null;
  try { party = await api.party(deal.party_id); } catch (_) { /* send without a number */ }

  const text = {
    party: await build('party', deal, settings, { party_phone: party && party.phone, revised }),
    internal: await build('internal', deal, settings, { party_phone: party && party.phone })
  };
  if (revised) text.party = text.party.replace('*SAUDA CONFIRMATION*', '*REVISED SAUDA CONFIRMATION*');
  let kind = 'party';
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
  const body = h('pre', { class: 'wa-msg' }, text.party);
  const tabs = {};
  const sendBtn = h('button', { type: 'button', class: 'wa-send', onclick: () => act() }, '');
  const copyBtn = h('button', { type: 'button', class: 'form-cancel', onclick: copy }, 'Copy');

  function paint() {
    body.textContent = text[kind];
    for (const [key, el] of Object.entries(tabs)) el.classList.toggle('on', key === kind);
    numberRow.hidden = kind !== 'party' || !!phone;
    sendBtn.textContent = kind === 'party' ? 'Send on WhatsApp' : 'Copy for the group';
    copyBtn.hidden = kind !== 'party';
    sub.textContent = kind === 'internal'
      ? 'For the firm — cost and margin. Copy it into your group.'
      : (phone ? `to ${deal.party_name} · +${phone}` : `${deal.party_name} has no WhatsApp number saved`);
  }

  const show = next => { kind = next; paint(); };
  const act = () => (kind === 'party' ? send() : copy());

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
      paint();
    }
    openWhatsApp(phone, text.party);
  }

  async function copy() {
    const what = text[kind];
    try { await navigator.clipboard.writeText(what); }
    catch (_) {
      const area = h('textarea', {}); area.value = what; document.body.appendChild(area);
      area.select(); document.execCommand('copy'); area.remove();
    }
    toast(kind === 'internal' ? 'Internal message copied — paste it in your group' : 'Sauda message copied');
  }

  // Both: the party's goes out on WhatsApp, then the internal one is ready to paste.
  async function both() {
    await send();
    show('internal');
    await copy();
  }

  const close = () => { overlay.remove(); document.removeEventListener('keydown', onKey); };
  const onKey = e => { if (e.key === 'Escape') close(); };
  const card = h('div', { class: 'modal-card wa-card' },
    h('div', { class: 'modal-head' },
      h('div', {}, h('div', { class: 'modal-title' },
        revised ? `${deal.ref} revised` : booked ? `${deal.ref} booked` : `Send ${deal.ref}`), sub),
      h('button', { type: 'button', class: 'modal-x', onclick: close }, '×')),
    h('div', { class: 'wa-tabs' },
      tabs.party = h('button', { type: 'button', class: 'wa-tab', onclick: () => show('party') }, 'To the party'),
      tabs.internal = h('button', { type: 'button', class: 'wa-tab', onclick: () => show('internal') }, 'Internal')),
    body,
    numberRow,
    h('div', { class: 'wa-actions' },
      sendBtn,
      h('button', { type: 'button', class: 'form-cancel', onclick: both }, 'Send both'),
      copyBtn,
      h('button', { type: 'button', class: 'form-cancel', onclick: close }, 'Done')));
  const overlay = h('div', { class: 'modal', onmousedown: e => { if (e.target === overlay) close(); } }, card);
  document.addEventListener('keydown', onKey);
  document.body.appendChild(overlay);
  paint();

  if (auto && phone && isPhone()) openWhatsApp(phone, text.party);
}
