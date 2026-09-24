/**
 * Sneferu preview-bootstrap bridge (cooperative-ui contract).
 *
 * The Sneferu preview launcher writes a versioned localStorage record
 * before redirecting to the application.  This bridge reads that record
 * and stores the session token so any client-side API call can attach
 * it as a Bearer header.
 *
 * Production CSP is `script-src 'none'` (FR-035) — this file is served
 * from /static/ but is NOT loaded by any HTML template, so it is inert
 * in production.  The Sneferu preview infrastructure may inject it when
 * the application runs inside the preview launcher.
 */
(function () {
  "use strict";
  try {
    var raw = localStorage.getItem('sneferu.preview.bootstrap.v1');
  } catch (e) {
    return;
  }
  if (!raw) return;
  try {
    var b = JSON.parse(raw);
  } catch (e) {
    return;
  }
  if (b && b.session_token) {
    window.__sneferuPreviewToken = b.session_token;
  }
})();
