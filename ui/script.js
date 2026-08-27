(function(){
  var sections = [
    {id:'hero', label:'Start'},
    {id:'architecture', label:'Architecture'},
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
  function countUp(el){
    var target = parseFloat(el.dataset.count);
    var decimals = parseInt(el.dataset.decimals || '0', 10);
    var suffix = el.dataset.suffix || '';
    if (reduceMotion) { el.textContent = target.toFixed(decimals) + suffix; return; }
    var start = null, dur = 1200;
    function step(ts){
      if (!start) start = ts;
      var p = Math.min((ts - start) / dur, 1);
      var eased = 1 - Math.pow(1 - p, 3);
      var val = target * eased;
      el.textContent = val.toFixed(decimals) + suffix;
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

  // split bar (architecture section's clean/auto-resolved/to-agent breakdown)
  var splitObs = new IntersectionObserver(function(entries){
    entries.forEach(function(e){
      if (!e.isIntersecting) return;
      splitObs.unobserve(e.target);
      document.getElementById('split-clean').style.width = '68.5%';
      document.getElementById('split-auto').style.width = '1.9%';
      document.getElementById('split-agent').style.width = '29.6%';
    });
  }, {threshold:0.3});
  splitObs.observe(document.getElementById('architecture'));
})();
