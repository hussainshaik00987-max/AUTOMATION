
(function(){
  var d=document;
  var yr=d.getElementById('yr'); if(yr) yr.textContent=new Date().getFullYear();

  // sticky header state
  var head=d.getElementById('siteHead');
  if(head){
    var onScroll=function(){head.dataset.stuck=window.scrollY>12?'1':'0';};
    onScroll(); addEventListener('scroll',onScroll,{passive:true});
  }

  // mobile nav
  var tog=d.getElementById('navToggle'), links=d.getElementById('navLinks');
  if(tog&&links){
    tog.addEventListener('click',function(){
      var open=tog.getAttribute('aria-expanded')==='true';
      tog.setAttribute('aria-expanded',String(!open));
      links.dataset.open=open?'0':'1';
    });
    links.addEventListener('click',function(ev){
      if(ev.target.tagName==='A'){tog.setAttribute('aria-expanded','false');links.dataset.open='0';}
    });
  }

  // scroll reveal
  var rv=d.querySelectorAll('.rv');
  if('IntersectionObserver' in window && !matchMedia('(prefers-reduced-motion:reduce)').matches){
    var io=new IntersectionObserver(function(es){
      es.forEach(function(en){ if(en.isIntersecting){en.target.classList.add('in');io.unobserve(en.target);} });
    },{rootMargin:'0px 0px -8% 0px',threshold:.08});
    rv.forEach(function(el){io.observe(el);});
  } else { rv.forEach(function(el){el.classList.add('in');}); }

  // gallery lightbox
  var lb=d.getElementById('lb'), stage=d.getElementById('lbStage'), cap=d.getElementById('lbCap');
  var tiles=[].slice.call(d.querySelectorAll('.tile')), idx=0;
  function show(i){
    if(!tiles.length) return;
    idx=(i+tiles.length)%tiles.length;
    var t=tiles[idx], art=t.querySelector('img,svg');
    if(art && art.tagName==='IMG'){
      // Swap in the wide rendition rather than upscaling the 700px tile.
      var big=d.createElement('img');
      big.src=t.dataset.full||art.currentSrc||art.src;
      big.alt=art.alt||''; big.decoding='async';
      stage.innerHTML=''; stage.appendChild(big);
    } else {
      stage.innerHTML = art?art.outerHTML:'';
    }
    cap.textContent = t.dataset.cap||'';
  }
  function open(i){ if(!lb) return; show(i); if(lb.showModal){lb.showModal();} else {lb.setAttribute('open','');} }
  function close(){ if(!lb) return; if(lb.close){lb.close();} else {lb.removeAttribute('open');} }
  tiles.forEach(function(t,i){ t.addEventListener('click',function(){open(i);}); });
  var c=d.getElementById('lbClose'); if(c) c.addEventListener('click',close);
  var p=d.getElementById('lbPrev'); if(p) p.addEventListener('click',function(){show(idx-1);});
  var n=d.getElementById('lbNext'); if(n) n.addEventListener('click',function(){show(idx+1);});
  if(lb){
    lb.addEventListener('click',function(ev){ if(ev.target===lb) close(); });
    d.addEventListener('keydown',function(ev){
      if(!lb.open) return;
      if(ev.key==='ArrowRight') show(idx+1);
      if(ev.key==='ArrowLeft') show(idx-1);
    });
  }

  
  // Modern Web Guidance: sync aria-invalid with :user-invalid state
  const syncAria = (el) => {
    if (el && el.matches) {
      el.setAttribute('aria-invalid', el.matches(':user-invalid') ? 'true' : 'false');
    }
  };
  d.addEventListener('blur', (e) => syncAria(e.target), true);
  d.addEventListener('input', (e) => {
    if (e.target.hasAttribute && e.target.hasAttribute('aria-invalid')) syncAria(e.target);
  });

  // enquiry form -> prefilled WhatsApp message (no backend needed)
  var f=d.getElementById('enq');
  if(f){
    f.addEventListener('submit',function(ev){
      ev.preventDefault();
      var status=d.getElementById('enqStatus');
      var name=(f.querySelector('#f-name')||{}).value||'';
      if(!name.trim()){
        if(status) status.textContent='Please add your name first.';
        (f.querySelector('#f-name')||{}).focus&&f.querySelector('#f-name').focus();
        return;
      }
      var lines=['Hi '+(f.dataset.name||'')+',',''];
      var labels=f.querySelectorAll('label');
      labels.forEach(function(l){
        var input=d.getElementById(l.getAttribute('for'));
        if(input&&input.value&&input.value.trim()){
          lines.push(l.textContent.trim()+': '+input.value.trim());
        }
      });
      lines.push('','(Sent from your website)');
      var body=lines.join('\n');
      var wa=f.dataset.wa;
      if(wa){
        window.open('https://wa.me/'+wa+'?text='+encodeURIComponent(body),'_blank','noopener');
        if(status) status.textContent='Opening WhatsApp…';
      } else if(f.dataset.email){
        window.location.href='mailto:'+f.dataset.email+'?subject='+
          encodeURIComponent('Website enquiry')+'&body='+encodeURIComponent(body);
        if(status) status.textContent='Opening your email app…';
      }
    });
  }
})();
