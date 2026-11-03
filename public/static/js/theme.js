(function(){var s=document.currentScript,d=document.documentElement,t;d.classList.add('js');
try{t=localStorage.getItem('theme')}catch(e){}
if(t!=='light'&&t!=='dark'){t=(s&&s.getAttribute('data-default'))||'system'}
if(t==='system'){t=window.matchMedia&&matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'}
d.setAttribute('data-theme',t)})();
