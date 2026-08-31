(function(){
  var sections = [
    {id:'hero', label:'Start'},
    {id:'architecture', label:'Architecture'},
    {id:'ai', label:'AI layer'},
    {id:'value', label:'Value'}
  ];
  var dotsNav = document.getElementById('dots');
  sections.forEach(function(s){
    var a = document.createElement('a');
    a.href = '#' + s.id; a.title = s.label; a.dataset.target = s.id;
    a.setAttribute('aria-label', 'Go to ' + s.label);
    dotsNav.appendChild(a);
  });
  var dotEls = Array.prototype.slice.call(dotsNav.querySelectorAll('a'));

  function onScroll(){
    var doc = document.documentElement;
    var scrolled = doc.scrollTop;
    var height = doc.scrollHeight - doc.clientHeight;
    document.getElementById('rail').style.width = (height > 0 ? (scrolled/height*100) : 0) + '%';

    var current = sections[0].id;
    sections.forEach(function(s){
      var el = document.getElementById(s.id);
      if (el && el.getBoundingClientRect().top < window.innerHeight * 0.5) current = s.id;
    });
    dotEls.forEach(function(d){ d.classList.toggle('active', d.dataset.target === current); });
  }
  document.addEventListener('scroll', onScroll, {passive:true});
  onScroll();

  // reveal-on-scroll
  var io = new IntersectionObserver(function(entries){
    entries.forEach(function(e){ if (e.isIntersecting) e.target.classList.add('in'); });
  }, {threshold:0.12});
  document.querySelectorAll('.reveal').forEach(function(el){ io.observe(el); });

  // count-up stats
  var reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  // Thousands separators, applied to the integer part only -- so the
  // animated value matches the real number already sitting in the HTML
  // (see the no-JS note below) instead of briefly rewriting "2,040" to
  // the comma-less "2040" mid-animation.
  function fmt(v, decimals){
    var parts = v.toFixed(decimals).split('.');
    parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ',');
    return parts.join('.');
  }
  function countUp(el){
    if (!el) return;  // a future .stat-cell without a [data-count] child degrades quietly
                       // instead of throwing inside the IntersectionObserver callback below
                       // (found via external review)
    var target = parseFloat(el.dataset.count);
    var decimals = parseInt(el.dataset.decimals || '0', 10);
    var suffix = el.dataset.suffix || '';
    if (reduceMotion) { el.textContent = fmt(target, decimals) + suffix; return; }
    var start = null, dur = 1200;
    function step(ts){
      if (!start) start = ts;
      var p = Math.min((ts - start) / dur, 1);
      var eased = 1 - Math.pow(1 - p, 3);
      var val = target * eased;
      el.textContent = fmt(val, decimals) + suffix;
      if (p < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  }
  var countObserved = new WeakSet();
  var cio = new IntersectionObserver(function(entries){
    entries.forEach(function(e){
      if (e.isIntersecting && !countObserved.has(e.target)) {
        countObserved.add(e.target);
        countUp(e.target.querySelector('[data-count]'));
      }
    });
  }, {threshold:0.4});
  document.querySelectorAll('.stat-cell').forEach(function(el){ cio.observe(el); });

  // split bar (architecture section's clean/auto-resolved/to-agent breakdown).
  // The real widths live inline in the HTML so the bar is still correct with
  // JS blocked/failed; because JS IS running here, collapse them to 0 first
  // so the CSS width transition has something to animate from on scroll.
  var splitTargets = {'split-clean':'67.4%', 'split-auto':'2.8%', 'split-agent':'29.8%'};
  Object.keys(splitTargets).forEach(function(id){
    var el = document.getElementById(id);
    if (el) el.style.width = '0%';
  });
  var splitObs = new IntersectionObserver(function(entries){
    entries.forEach(function(e){
      if (!e.isIntersecting) return;
      splitObs.unobserve(e.target);
      Object.keys(splitTargets).forEach(function(id){
        var el = document.getElementById(id);
        if (el) el.style.width = splitTargets[id];
      });
    });
  }, {threshold:0.3});
  splitObs.observe(document.getElementById('architecture'));
})();
