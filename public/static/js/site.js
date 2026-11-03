(function(){
  var d=document,root=d.documentElement,ls={get:function(k){try{return localStorage.getItem(k)}catch(e){return null}},set:function(k,v){try{localStorage.setItem(k,v)}catch(e){}}};
  var today=d.getElementById('today');
  if(today)today.textContent=new Date().toLocaleDateString('en-GB',{weekday:'long',day:'numeric',month:'long',year:'numeric'});

  var tg=d.getElementById('theme-toggle');
  if(tg)tg.addEventListener('click',function(){var n=root.getAttribute('data-theme')==='dark'?'light':'dark';root.setAttribute('data-theme',n);ls.set('theme',n)});

  var mb=d.getElementById('menu-btn'),nav=d.getElementById('primary-nav');
  if(mb&&nav){
    mb.addEventListener('click',function(){var o=nav.classList.toggle('open');mb.setAttribute('aria-expanded',o?'true':'false')});
    d.addEventListener('keydown',function(e){if(e.key==='Escape'){nav.classList.remove('open');mb.setAttribute('aria-expanded','false');var m=d.querySelector('.more[open]');if(m){m.removeAttribute('open');m.querySelector('summary').focus()}}});
    d.addEventListener('click',function(e){var m=d.querySelector('.more[open]');if(m&&!m.contains(e.target))m.removeAttribute('open')});
  }

  d.querySelectorAll('form.newsletter').forEach(function(f){
    f.addEventListener('submit',function(e){
      e.preventDefault();var msg=f.querySelector('.msg');msg.textContent='Sending…';
      fetch(f.action,{method:'POST',body:new FormData(f),headers:{'Accept':'application/json'}})
        .then(function(r){return r.json()}).then(function(j){msg.textContent=j.message;if(j.ok)f.reset()})
        .catch(function(){msg.textContent='Something went wrong. Please try again.'});
    });
  });

  d.querySelectorAll('.copy-link').forEach(function(b){
    b.addEventListener('click',function(){
      var u=b.getAttribute('data-url')||location.href;
      (navigator.clipboard?navigator.clipboard.writeText(u):Promise.reject()).then(function(){b.textContent='Copied ✓'},function(){window.prompt('Copy this link',u)});
      setTimeout(function(){b.textContent='Copy link'},2000);
    });
  });

  d.querySelectorAll('.print-btn').forEach(function(b){b.addEventListener('click',function(){window.print()})});

  // First-party, cookieless page-view beacon (skipped for Do-Not-Track, previews and error pages)
  if(!d.body.hasAttribute('data-noview')&&navigator.sendBeacon&&navigator.doNotTrack!=='1'){
    var a=parseInt(d.body.getAttribute('data-article'),10);
    var ads=[].slice.call(d.querySelectorAll('[data-ad]')).map(function(e){return parseInt(e.getAttribute('data-ad'),10)}).filter(function(n){return !isNaN(n)});
    navigator.sendBeacon('/api/pv',new Blob([JSON.stringify({p:location.pathname,a:isNaN(a)?null:a,r:d.referrer||'',ads:ads})],{type:'application/json'}));
  }

  // Optional Google Analytics, only after explicit consent
  var gaId=(d.currentScript||{}).getAttribute&&d.currentScript.getAttribute('data-ga'),banner=d.getElementById('consent');
  function loadGA(){
    if(!gaId||window.__gaLoaded)return;window.__gaLoaded=true;
    window.dataLayer=window.dataLayer||[];window.gtag=function(){dataLayer.push(arguments)};
    gtag('js',new Date());gtag('config',gaId,{anonymize_ip:true});
    var s=d.createElement('script');s.async=true;s.src='https://www.googletagmanager.com/gtag/js?id='+encodeURIComponent(gaId);d.head.appendChild(s);
  }
  if(gaId){
    var c=ls.get('consent');
    if(c==='granted')loadGA();else if(!c&&banner)banner.hidden=false;
    if(banner)banner.addEventListener('click',function(e){var v=e.target.getAttribute('data-consent');if(!v)return;ls.set('consent',v);banner.hidden=true;if(v==='granted')loadGA()});
  }
})();
