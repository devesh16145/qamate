"""Opt-in pytest readback fault injection for isolated Atomic CRM demo replays.

These simulate faulty rendered readbacks, not changes to the remote application
or proof of backend storage fault coverage. Generated tests are never edited.
"""
import json
import os
from pathlib import Path

import pytest


FAULT_JS = r"""mode => {
  if (location.origin !== 'https://marmelab.com') return;
  let revisedSeen = false, returnedToList = false, running = false;
  window.__qamateFault = {mode, injections: 0};
  function inject() {
    if (running || !document.body || !location.pathname.startsWith('/atomic-crm-demo/')) return;
    running = true;
    try {
      const route = location.hash.split('?')[0];
      const companyDetail = /^#\/companies\/[^/]+\/show(?:\/contacts)?$/.test(route);
      if (companyDetail && document.body.innerText.includes('Qamate Revised City')) revisedSeen = true;
      if (revisedSeen && route === '#/companies') returnedToList = true;
      let swaps = [];
      if (mode === 'wrong_saved_values' && companyDetail)
        swaps = [['Qamate Test City', 'Qamate WRONG City']];
      if (mode === 'failed_persistence' && companyDetail && returnedToList)
        swaps = [['Qamate Revised City', 'Qamate Test City'], ['Qamate revised description', 'Qamate original description']];
      if (mode === 'incorrect_relationship' && /^#\/contacts\/[^/]+\/show$/.test(route))
        swaps = [['QAMATE Relationship 9f58c327', 'QAMATE WRONG Relationship']];
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      let node;
      while ((node = walker.nextNode())) {
        if (node.parentElement?.closest('script,style,input,textarea,select')) continue;
        for (const [expected, faulty] of swaps) {
          if (node.textContent.trim() === expected) {
            node.textContent = node.textContent.replace(expected, faulty);
            window.__qamateFault.injections++;
          }
        }
      }
    } finally { running = false; }
  }
  new MutationObserver(inject).observe(document, {subtree:true, childList:true, characterData:true});
  addEventListener('hashchange', inject);
  setInterval(inject, 25);
}"""


@pytest.fixture(autouse=True)
def isolated_crm_readback_fault(page):
    mode = os.environ.get('QAMATE_CRM_FAULT')
    if mode not in {'control', 'wrong_saved_values', 'failed_persistence', 'incorrect_relationship'}:
        raise ValueError('Explicit isolated CRM fault mode required')
    page.add_init_script('(' + FAULT_JS + ')(' + json.dumps(mode) + ')')
    yield
    evidence = {'mode': mode, 'injections': 0}
    try:
        evidence.update(page.evaluate('window.__qamateFault || {}'))
    except Exception as exc:
        evidence['capture_error'] = type(exc).__name__
    Path(os.environ['QAMATE_CRM_FAULT_EVIDENCE']).write_text(json.dumps(evidence), encoding='utf-8')
