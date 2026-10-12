/* The guided tour: a walk through the operator pages, pointing at the real
 * controls on the real page rather than at pictures of them.
 *
 * Shared by /status, /tune and /settings. It is stitched into each of those
 * pages by the relay (see expand_includes in relay/server.py) rather than
 * loaded with a <script src>, because the pages' Content-Security-Policy
 * names hashes of their own inline scripts and nothing else.
 *
 * The tour spans three pages, so its position is kept in localStorage and
 * picked up again after the navigation. Whether it has ever been finished
 * lives on the relay instead, in event.toml beside setup.completed: an app
 * window and an ordinary browser tab do not always share storage, and being
 * shown round a second time because you opened the other one is the kind of
 * thing that reads as a bug.
 */
(function () {
  'use strict';

  var KEY = 'cc.tour.v1';          // {i: <step index>} while a tour is running
  var PATH = location.pathname.replace(/\/+$/, '') || '/status';

  // ---- the tour ---------------------------------------------------------
  // target: a selector on that page. A step whose target is not there (a
  // panel this build does not have) is skipped rather than shown pointing
  // at nothing. No target at all means a centred card, for the two steps
  // that are talking about the whole page.
  var STEPS = [
    {
      page: '/status', chapter: 'Welcome',
      title: 'A quick tour',
      body: 'This walks through the three pages you will use: the console ' +
            'you watch during an event, the tuner that sizes the captions ' +
            'for your screen, and the settings. It takes about two minutes, ' +
            'and you can leave at any point with <b>Skip</b>.',
      before: openPane('console')
    },
    {
      page: '/status', chapter: 'Console', target: '#checklist',
      title: 'The three things that have to be right',
      body: 'Before an event, this is the whole job. Each row turns green ' +
            'when it is ready: a microphone that can hear the room, an ' +
            'engine to turn speech into words, and at least one screen ' +
            'listening. A red mark here is the reason captions will not ' +
            'appear, and it says which one.',
      before: openPane('console')
    },
    {
      page: '/status', chapter: 'Console', target: '#audio-device',
      title: 'Which microphone',
      body: 'Pick the input that carries the room — the desk feed, not the ' +
            'laptop lid. The bar above moves while someone talks; if it ' +
            'stays flat you are on the wrong input. <b>Refresh</b> after ' +
            'plugging something in.'
    },
    {
      page: '/status', chapter: 'Console', target: '#backend-select',
      title: 'Which engine',
      body: 'How speech becomes text. Only the options this computer can ' +
            'actually run are listed. Practice mode makes captions up, for ' +
            'rehearsing the setup without spending anything.'
    },
    {
      page: '/status', chapter: 'Console', target: '#btn-golive',
      title: 'The one button',
      body: 'Go Live starts listening <i>and</i> shows the captions to the ' +
            'audience, in one press. That is the ordinary path, and most ' +
            'events never need anything else.'
    },
    {
      page: '/status', chapter: 'Console', target: '#btn-open',
      title: 'Or just the captions',
      body: 'Starting captions on their own lets you check levels and ' +
            'accuracy while the audience still sees the welcome screen. ' +
            'When the program begins, <b>Go live to audience</b> on the ' +
            'live screen hands it over to them.'
    },
    {
      page: '/status', chapter: 'Console', target: '#btn-preflight',
      title: 'The check before the doors open',
      body: 'Run this twenty minutes before an event. It checks the ' +
            'microphone, the engine, the network and anything you have ' +
            'connected, and tells you what to do about whatever it finds. ' +
            'Much better than discovering it from an empty screen.'
    },
    {
      page: '/status', chapter: 'Console', target: '#audience-link',
      title: 'The link to hand out',
      body: 'This is the address an attendee opens on their own phone, and ' +
            'the one behind the QR code. Copy it into a slide, a program, or ' +
            'the chat of whatever is streaming.'
    },
    {
      page: '/status', chapter: 'Console', target: '#nav-transcript',
      title: 'Everything gets kept',
      body: 'Every caption is recorded and can be downloaded afterwards as ' +
            'subtitles or plain text. If the event is also being filmed, ' +
            '<b>Mark video start</b> in here lines the subtitle timings up ' +
            'with the video file.',
      before: openPane('transcript')
    },
    {
      page: '/status', chapter: 'Console', target: '#url-wall',
      title: 'The screens',
      body: 'The wall address is what ProPresenter, OBS or a projector ' +
            'opens. Both addresses work from any device on the same ' +
            'network — that is all a screen needs to start showing ' +
            'captions.',
      before: openPane('screens')
    },
    {
      page: '/tune', chapter: 'Tuner',
      title: 'Sizing the captions',
      body: 'This page exists because every room is different. Set the ' +
            'captions up once for your screen here, and the address at the ' +
            'top carries those settings with it.'
    },
    {
      page: '/tune', chapter: 'Tuner', target: '#controls',
      title: 'The controls',
      body: 'Text size, how many lines, colours, alignment and how long a ' +
            'line stays up. Nothing here is saved to the system - each ' +
            'change simply rewrites the address at the top of the page.'
    },
    {
      page: '/tune', chapter: 'Tuner', target: '#stage',
      title: 'The preview',
      body: 'A live preview at the shape of screen you picked, with sample ' +
            'text you can replace with something from your own event. Judge ' +
            'the size from the back of the room, not from this desk.'
    },
    {
      page: '/tune', chapter: 'Tuner', target: '#url-card',
      title: 'The address to copy',
      body: 'This is the whole point of the page. Copy it and paste it into ' +
            'ProPresenter as a web element, or into OBS as a browser ' +
            'source. The <b>Copy</b> button turns amber if you change a ' +
            'setting after copying, so the address in your hand is never ' +
            'quietly out of date.'
    },
    {
      page: '/settings', chapter: 'Settings', target: '#tabs',
      title: 'Everything else',
      body: 'Five tabs, grouped by who the setting is for: the ' +
            '<b>Event</b> itself, how the words are <b>Captioned</b>, what ' +
            'the <b>Audience</b> gets, the <b>Outputs</b> you feed, and the ' +
            '<b>System</b> underneath. Nothing here needs a config file.'
    },
    {
      page: '/settings', chapter: 'Settings', target: '#panel-keyterms',
      title: 'Names and terms to get right',
      body: 'The single most effective setting in the app. Add the names of ' +
            'your speakers, your building, your ministries - anything a ' +
            'stranger would spell wrong - and the engine will get them ' +
            'right. Type the word, pick what kind of thing it is, add it.',
      before: openTab('#tab-captions')
    },
    {
      page: '/settings', chapter: 'Settings', target: '#panel-blocklist',
      title: 'Words to keep off the screen',
      body: 'Anything listed here never reaches the wall or a phone, however ' +
            'it was heard. The list stays hidden until you ask to see it, ' +
            'because it is not a list anyone wants on a projector by ' +
            'accident.',
      before: openTab('#tab-captions')
    },
    {
      page: '/settings', chapter: 'Settings', target: '#panel-tuning',
      title: 'Recognition tuning',
      body: 'How eagerly the engine commits to what it heard. The defaults ' +
            'are marked, and they suit most rooms - come here when captions ' +
            'lag behind the speaker, or arrive in fragments, and change one ' +
            'thing at a time.',
      before: openTab('#tab-captions')
    },
    {
      page: '/settings', chapter: 'Settings', target: '#panel-slides',
      title: 'The audience view',
      body: 'What someone sees on their own phone: the captions, and ' +
            'optionally the slide currently on the screen, so a person at ' +
            'the back can read both from where they are sitting.',
      before: openTab('#tab-audience')
    },
    {
      page: '/settings', chapter: 'Settings', target: '#panel-pp',
      title: 'ProPresenter and other outputs',
      body: 'If the room already runs ProPresenter, the relay can talk to it ' +
            'directly. Alongside it are the keys for OBS-style overlays and ' +
            'the YouTube upload, for events that are also streamed.',
      before: openTab('#tab-outputs')
    },
    {
      page: '/settings', chapter: 'Settings', target: '#panel-window',
      title: 'How the app opens',
      body: 'Whether these pages open as their own window, like a program, ' +
            'or as an ordinary browser tab. The window is easier to find ' +
            'again mid-event; the tab is easier if you like your bookmarks. ' +
            'Change it whenever you like.',
      before: openTab('#tab-system')
    },
    {
      page: '/settings', chapter: 'Settings',
      title: "That's the tour",
      body: 'You can run through it again at any time with <b>Take the ' +
            'tour</b> at the top of the status, tuner and settings pages. ' +
            'The wizard that first set this machine up is still there too, ' +
            'under <b>Run setup again</b>.',
      last: true
    }
  ];

  // ---- things a step may need doing before it can be pointed at ---------

  function openDisclosure(sel) {
    return function () {
      var head = document.querySelector(sel);
      if (head && head.getAttribute('aria-expanded') === 'false') head.click();
    };
  }

  // The console switches panes in the page rather than loading three pages,
  // so a step that talks about the transcript has to put that pane on screen
  // first. Clicking the rail button rather than setting hidden keeps the
  // page's own bookkeeping - the remembered pane, the highlighted nav - in
  // step with what the tour just did.
  function openPane(name) {
    return function () {
      var nav = document.getElementById('nav-' + name);
      var pane = document.getElementById('pane-' + name);
      if (nav && pane && pane.hidden) nav.click();
    };
  }

  function openTab(sel) {
    return function () {
      var tab = document.querySelector(sel);
      if (tab && tab.getAttribute('aria-selected') !== 'true') tab.click();
    };
  }

  // ---- state ------------------------------------------------------------

  function saved() {
    try {
      var raw = localStorage.getItem(KEY);
      return raw ? JSON.parse(raw) : null;
    } catch (e) { return null; }
  }

  function remember(i) {
    try { localStorage.setItem(KEY, JSON.stringify({ i: i })); } catch (e) {}
  }

  function forget() {
    try { localStorage.removeItem(KEY); } catch (e) {}
  }

  function markSeen(done) {
    // Best effort: a tour that cannot record itself is not worth an error
    // message over a dashboard.
    try {
      fetch('/api/setup', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tour_completed: !!done })
      }).catch(function () {});
    } catch (e) {}
  }

  // ---- the overlay ------------------------------------------------------

  var ui = null;      // built on first use, then reused
  var at = -1;        // index of the step on screen, -1 when closed
  var raf = 0;
  var lastBox = '';

  var CSS = [
    // The veil is a click blocker first and a dimmer second: while a step
    // has a target, the ring's own outer shadow does the dimming, and a
    // second layer on top of it only makes the page unreadable.
    '.tour-veil{position:fixed;inset:0;z-index:9000;background:transparent;}',
    '.tour-veil.solid{background:rgba(0,0,0,.66);}',
    '.tour-ring{position:absolute;z-index:9001;border-radius:14px;pointer-events:none;',
    '  box-shadow:0 0 0 3px #0A84FF,0 0 0 9999px rgba(0,0,0,.68);transition:',
    '  top 180ms ease,left 180ms ease,width 180ms ease,height 180ms ease;}',
    '.tour-card{position:absolute;z-index:9002;width:min(390px,calc(100vw - 32px));',
    '  background:rgba(28,28,30,.96);color:#F5F5F7;',
    '  border:0.5px solid rgba(255,255,255,.12);border-radius:20px;',
    '  padding:18px 20px 16px;box-shadow:0 18px 44px rgba(0,0,0,.6);',
    '  -webkit-backdrop-filter:blur(24px);backdrop-filter:blur(24px);',
    '  font:15px/1.5 -apple-system,BlinkMacSystemFont,"SF Pro Text","Figtree",',
    '  "Segoe UI",system-ui,sans-serif;}',
    '.tour-card.centred{position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);',
    '  width:min(440px,calc(100vw - 32px));}',
    '.tour-chapter{font-size:12px;letter-spacing:.02em;text-transform:uppercase;',
    '  color:#8E8E93;font-weight:500;margin-bottom:6px;}',
    '.tour-card h3{margin:0 0 8px;font-size:19px;line-height:1.25;color:#fff;',
    '  font-weight:600;letter-spacing:-0.015em;}',
    '.tour-card p{margin:0;color:#C7C7CC;}',
    '.tour-card b{color:#fff;font-weight:600;}',
    '.tour-foot{display:flex;align-items:center;gap:8px;margin-top:16px;}',
    '.tour-count{flex:1;font-size:13px;color:#8E8E93;}',
    '.tour-card button{font:inherit;font-size:14px;font-weight:600;height:36px;',
    '  padding:0 16px;border-radius:18px;border:none;background:#2C2C2E;',
    '  color:#0A84FF;cursor:pointer;}',
    '.tour-card button:hover{background:#3A3A3C;}',
    '.tour-card button.go{background:#0A84FF;color:#fff;}',
    '.tour-card button.go:hover{background:#5AB0FF;}',
    '.tour-card button.quiet{background:none;color:#8E8E93;}',
    '.tour-card button.quiet:hover{color:#F5F5F7;background:#2C2C2E;}',
    '.tour-card :focus-visible{outline:2px solid #5AB0FF;outline-offset:2px;}',
    '@media (prefers-reduced-motion: reduce){.tour-ring{transition:none;}}'
  ].join('');

  function build() {
    var style = document.createElement('style');
    style.textContent = CSS;
    document.head.appendChild(style);

    var veil = document.createElement('div');
    veil.className = 'tour-veil';

    var ring = document.createElement('div');
    ring.className = 'tour-ring';
    ring.hidden = true;

    var card = document.createElement('div');
    card.className = 'tour-card';
    card.setAttribute('role', 'dialog');
    card.setAttribute('aria-modal', 'true');
    card.innerHTML =
      '<div class="tour-chapter"></div><h3></h3><p></p>' +
      '<div class="tour-foot"><span class="tour-count"></span>' +
      '<button type="button" class="quiet" data-act="skip">Skip</button>' +
      '<button type="button" data-act="back">Back</button>' +
      '<button type="button" class="go" data-act="next">Next</button></div>';

    document.body.appendChild(veil);
    document.body.appendChild(ring);
    document.body.appendChild(card);

    card.addEventListener('click', function (ev) {
      var act = ev.target && ev.target.getAttribute('data-act');
      if (act === 'next') go(at + 1);
      else if (act === 'back') go(at - 1);
      else if (act === 'skip') close(false);
    });
    veil.addEventListener('click', function () { /* swallow */ });

    ui = {
      veil: veil, ring: ring, card: card,
      chapter: card.querySelector('.tour-chapter'),
      title: card.querySelector('h3'),
      body: card.querySelector('p'),
      count: card.querySelector('.tour-count'),
      back: card.querySelector('[data-act="back"]'),
      next: card.querySelector('[data-act="next"]')
    };
    document.addEventListener('keydown', onKey, true);
    return ui;
  }

  function onKey(ev) {
    if (at < 0) return;
    if (ev.key === 'Escape') { ev.preventDefault(); close(false); }
    else if (ev.key === 'ArrowRight' || ev.key === 'Enter') { ev.preventDefault(); go(at + 1); }
    else if (ev.key === 'ArrowLeft') { ev.preventDefault(); go(at - 1); }
  }

  // ---- moving between steps ---------------------------------------------

  function stepAt(i) { return (i >= 0 && i < STEPS.length) ? STEPS[i] : null; }

  function showable(step) {
    if (!step) return false;
    if (step.page !== PATH) return true;            // decided after the hop
    if (!step.target) return true;
    var el = document.querySelector(step.target);
    if (!el) return false;
    // Present but not on screen is as useless as absent: the console keeps
    // its live screen in the DOM while the ready screen is showing, and a
    // ring drawn round a zero-sized box is worse than skipping the step.
    // A step with a `before` gets the benefit of the doubt - that hook is
    // what is about to make it visible.
    if (step.before) return true;
    return el.getClientRects().length > 0;
  }

  function go(i, dir) {
    dir = dir || (i >= at ? 1 : -1);
    // Walk past anything this page cannot show - a panel a build does not
    // have should not become a step pointing at empty space.
    while (i >= 0 && i < STEPS.length && !showable(stepAt(i))) i += dir;
    if (i < 0) return;
    if (i >= STEPS.length) { close(true); return; }

    var step = STEPS[i];
    if (step.page !== PATH) {         // continue on the page it talks about
      remember(i);
      location.href = step.page;
      return;
    }
    at = i;
    remember(i);
    if (step.before) { try { step.before(); } catch (e) {} }
    render(step, i);
  }

  function render(step, i) {
    var u = ui || build();
    u.chapter.textContent = step.chapter || '';
    u.title.innerHTML = step.title;
    u.body.innerHTML = step.body;
    u.count.textContent = 'Step ' + (i + 1) + ' of ' + STEPS.length;
    u.back.hidden = (i === 0);
    u.next.textContent = step.last ? 'Finish' : 'Next';
    u.veil.hidden = false;
    u.card.hidden = false;

    var el = step.target ? document.querySelector(step.target) : null;
    if (el && el.scrollIntoView) {
      var r = el.getBoundingClientRect();
      // Leave the page where it is when the target is already on screen.
      // The tuner in particular is laid out so its sliders and its preview
      // fit together without scrolling, and centring each one in turn would
      // undo exactly that.
      if (r.top < 0 || r.bottom > innerHeight) {
        // 'nearest', not 'center': the least scrolling that brings the
        // target into view keeps the page's own header - and whatever the
        // last step pointed at - where the reader last saw them. Anything
        // taller than the window cannot be brought in whole, so that goes
        // by its top edge.
        el.scrollIntoView({
          block: r.height > innerHeight ? 'start' : 'nearest',
          behavior: 'smooth'
        });
      }
    }
    lastBox = '';
    place(step);
    if (!raf) raf = requestAnimationFrame(follow);
  }

  // The page moves under the tour: disclosures animate open, live tiles
  // change width, a smooth scroll is still running. Rather than guess how
  // long any of that takes, re-measure every frame and only write when the
  // target has actually moved.
  function follow() {
    raf = 0;
    if (at < 0) return;
    place(STEPS[at]);
    raf = requestAnimationFrame(follow);
  }

  function place(step) {
    var u = ui;
    var el = step.target ? document.querySelector(step.target) : null;
    if (!el) {
      if (lastBox === 'centred') return;
      lastBox = 'centred';
      u.ring.hidden = true;
      u.veil.className = 'tour-veil solid';
      u.card.className = 'tour-card centred';
      u.card.style.top = u.card.style.left = '';
      return;
    }
    var r = el.getBoundingClientRect();
    var key = [r.top, r.left, r.width, r.height, innerWidth, innerHeight].join(',');
    if (key === lastBox) return;
    lastBox = key;

    var pad = 8;
    u.veil.className = 'tour-veil';
    u.ring.hidden = false;
    u.ring.style.top = (r.top + scrollY - pad) + 'px';
    u.ring.style.left = (r.left + scrollX - pad) + 'px';
    u.ring.style.width = (r.width + pad * 2) + 'px';
    u.ring.style.height = (r.height + pad * 2) + 'px';

    u.card.className = 'tour-card';
    var cw = u.card.offsetWidth, ch = u.card.offsetHeight, gap = 14;
    // Below the target if it fits, above if that fits better, otherwise
    // beside it - and never off an edge.
    var top;
    if (r.bottom + gap + ch < innerHeight) top = r.bottom + gap;
    else if (r.top - gap - ch > 0) top = r.top - gap - ch;
    else top = Math.max(12, Math.min(innerHeight - ch - 12, r.top));
    var left = r.left + r.width / 2 - cw / 2;
    left = Math.max(12, Math.min(innerWidth - cw - 12, left));
    u.card.style.top = (top + scrollY) + 'px';
    u.card.style.left = (left + scrollX) + 'px';
  }

  function close(finished) {
    at = -1;
    forget();
    if (raf) { cancelAnimationFrame(raf); raf = 0; }
    if (ui) { ui.veil.hidden = true; ui.ring.hidden = true; ui.card.hidden = true; }
    // Skipping counts as seen. Someone who has said no once should not be
    // asked again every launch; the button in the header is how they come
    // back to it.
    markSeen(true);
    if (finished && PATH !== '/status') location.href = '/status';
  }

  function start(from) {
    if (typeof from !== 'number') from = 0;
    go(from, 1);
  }

  // ---- getting started --------------------------------------------------

  function firstStepOn(path) {
    for (var i = 0; i < STEPS.length; i++) if (STEPS[i].page === path) return i;
    return 0;
  }

  function wireStartButtons() {
    var buttons = document.querySelectorAll('[data-tour-start]');
    for (var i = 0; i < buttons.length; i++) {
      buttons[i].addEventListener('click', function (ev) {
        ev.preventDefault();
        start(0);
      });
    }
  }

  function boot() {
    wireStartButtons();
    var state = saved();
    if (state && typeof state.i === 'number') {   // mid-tour, mid-navigation
      start(state.i);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }

  // The status page calls this once it knows, from the relay, whether anyone
  // has been shown round this machine yet. Only that page offers by itself:
  // it is where a first launch lands, and a tour that started itself over
  // the settings page would be one somebody navigated to on purpose.
  window.CampusTour = {
    start: start,
    offerIfUnseen: function (seen) {
      if (seen || saved() || at >= 0) return;
      start(firstStepOn('/status'));
    }
  };
})();
