(function(){
  var d=document,token=(d.querySelector('meta[name=csrf-token]')||{}).content;
  function $(s,r){return (r||d).querySelector(s)}function $$(s,r){return Array.prototype.slice.call((r||d).querySelectorAll(s))}
  // confirm dialogs (no inline handlers: CSP)
  $$('form[data-confirm]').forEach(function(f){f.addEventListener('submit',function(e){if(!confirm(f.getAttribute('data-confirm')))e.preventDefault()})});
  $$('.urlbox').forEach(function(i){i.addEventListener('focus',function(){i.select()})});
  var st=$('#side-toggle'),side=$('#side');
  if(st)st.addEventListener('click',function(){var o=side.classList.toggle('open');st.setAttribute('aria-expanded',o)});
  d.addEventListener('click',function(e){var a=$('.acct[open]');if(a&&!a.contains(e.target))a.removeAttribute('open')});

  // ---- media picker ----
  var dlg=$('#picker'),grid=$('#picker-grid'),more=$('#picker-more'),pq=$('#picker-q'),pmsg=$('#picker-msg'),onPick=null,page=1,term='';
  function load(reset){
    if(reset){page=1;grid.innerHTML=''}
    fetch('/admin/media/picker?page='+page+'&q='+encodeURIComponent(term),{headers:{Accept:'application/json'}}).then(function(r){return r.json()}).then(function(j){
      j.items.forEach(function(m){
        var b=d.createElement('button');b.type='button';b.innerHTML='<img alt="" loading="lazy"><span></span>';
        b.querySelector('img').src=m.thumb;b.querySelector('span').textContent=m.alt||m.filename;
        b.addEventListener('click',function(){dlg.close();if(onPick)onPick(m)});grid.appendChild(b);
      });
      more.hidden=!j.has_next;pmsg.textContent=grid.children.length?'':'No images yet \u2014 upload one.';
    });
  }
  function openPicker(cb){if(!dlg)return;onPick=cb;term=pq?pq.value:'';dlg.showModal();load(true)}
  if(dlg){
    $('[data-close]',dlg).addEventListener('click',function(){dlg.close()});
    more.addEventListener('click',function(){page++;load(false)});
    var t;pq.addEventListener('input',function(){clearTimeout(t);t=setTimeout(function(){term=pq.value;load(true)},250)});
    var up=$('#picker-upload');
    if(up)up.addEventListener('change',function(){if(up.files[0])uploadFiles(up.files,function(){up.value='';load(true)},pmsg)});
  }
  function uploadFiles(files,done,msg){
    var fd=new FormData();for(var i=0;i<files.length;i++)fd.append('file',files[i]);
    if(msg)msg.textContent='Uploading and optimising\u2026';
    fetch('/admin/upload',{method:'POST',body:fd,headers:{'X-CSRFToken':token,Accept:'application/json'}})
      .then(function(r){return r.json().then(function(j){return{ok:r.ok,j:j}})})
      .then(function(x){if(!x.ok)throw new Error(x.j.error||'Upload failed');if(msg)msg.textContent='Uploaded.';done(x.j)})
      .catch(function(e){if(msg)msg.textContent=e.message});
  }
  $$('.media-field').forEach(function(w){
    var input=$('#'+w.getAttribute('data-target')),img=$('.media-preview',w),mode=w.getAttribute('data-mode');
    $('[data-pick]',w).addEventListener('click',function(){openPicker(function(m){input.value=mode==='id'?m.id:m.url;img.src=m.thumb;img.hidden=false;input.dispatchEvent(new Event('change',{bubbles:true}))})});
    var c=$('[data-clear]',w);if(c)c.addEventListener('click',function(){input.value='';img.hidden=true;img.removeAttribute('src');input.dispatchEvent(new Event('change',{bubbles:true}))});
  });
  var lib=$('#lib-upload');
  if(lib)lib.addEventListener('change',function(){uploadFiles(lib.files,function(){location.reload()},$('#lib-msg'))});

  // ---- gallery ----
  var gl=$('#gallery-list'),gi=$('#gallery_ids');
  function sync(){gi.value=$$('li',gl).map(function(li){return li.getAttribute('data-id')}).join(',');gi.dispatchEvent(new Event('change',{bubbles:true}))}
  if(gl){
    gl.addEventListener('click',function(e){var li=e.target.closest('li');if(!li)return;
      if(e.target.hasAttribute('data-rm')){li.remove();sync()}
      if(e.target.hasAttribute('data-up')&&li.previousElementSibling){gl.insertBefore(li,li.previousElementSibling);sync()}});
    $('#gallery-add').addEventListener('click',function(){openPicker(function(m){
      if($$('li',gl).some(function(li){return li.getAttribute('data-id')==m.id}))return;
      var li=d.createElement('li');li.setAttribute('data-id',m.id);
      li.innerHTML='<img alt="" width="64" height="48"><span class="grow"></span><button type="button" data-up title="Move up">\u2191</button><button type="button" data-rm title="Remove">\u00d7</button>';
      li.querySelector('img').src=m.thumb;li.querySelector('span').textContent=m.filename;gl.appendChild(li);sync()})});
  }

  // ---- rich-text article editor (Quill) + autosave ----
  var form=$('#article-form'),host=$('#editor');
  if(form&&host&&window.Quill){
    var ta=$('#body_html'),statusEl=$('#save-status'),locked=form.getAttribute('data-locked')==='1';
    var quill=new Quill(host,{theme:'snow',placeholder:'Write the article\u2026',modules:{toolbar:{container:'#toolbar',handlers:{image:function(){
      openPicker(function(m){var r=quill.getSelection(true)||{index:quill.getLength()};
        quill.insertEmbed(r.index,'image',m.url,'user');quill.formatText(r.index,1,'alt',m.alt||'','user');quill.setSelection(r.index+1,0,'user')})}}}}});
    quill.setContents(quill.clipboard.convert({html:ta.value}),'silent');
    if(locked)quill.enable(false);
    var tb=$('#toolbar');if(tb)tb.classList.add('ql-toolbar-host');host.classList.add('quill-host');
    var aid=parseInt(form.getAttribute('data-article-id'),10)||null,ver=form.getAttribute('data-version')||'',
        auto=form.getAttribute('data-autosave')==='1'&&!locked,dirty=false,saving=false,timer=null,again=false;
    function setStatus(text,cls){if(!statusEl)return;statusEl.textContent=text;statusEl.className='save-status'+(cls?' '+cls:'')}
    function html(){return quill.getSemanticHTML()}
    function body(){var h=html();return h==='<p></p>'?'':h}
    function payload(){return {id:aid,v:ver,title:$('#title').value,subtitle:$('#subtitle').value,excerpt:$('#excerpt').value,body_html:body(),sources_md:$('#sources_md').value}}
    function schedule(){if(!auto)return;clearTimeout(timer);timer=setTimeout(save,3500)}
    function touched(){dirty=true;if(auto){setStatus('Unsaved changes\u2026','warn');schedule()}else if(!locked){setStatus('Unsaved changes. Use Save.','warn')}}
    function save(){
      if(!auto||!dirty)return;if(saving){again=true;return}
      saving=true;setStatus('Saving\u2026');
      fetch('/admin/articles/autosave',{method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':token,Accept:'application/json'},body:JSON.stringify(payload())})
        .then(function(r){return r.json().then(function(j){return{status:r.status,j:j}})})
        .then(function(x){
          saving=false;
          if(x.status===200){
            var first=!aid;aid=x.j.id;ver=x.j.v;dirty=again;
            if(first){form.setAttribute('action',x.j.edit_url);  /* NOT form.action: buttons named "action" shadow that property */ form.setAttribute('data-article-id',aid);history.replaceState(null,'',x.j.edit_url)}
            setStatus('Saved at '+x.j.saved_at,'ok');
            if(again){again=false;dirty=true;schedule()}
          }else if(x.status===409||x.status===403){auto=false;setStatus(x.j.error||'Autosave stopped.','err')}
          else{setStatus(x.j.error||'Autosave failed.','err')}
        }).catch(function(){saving=false;setStatus('Autosave failed (offline?). Your text is still in the editor; use Save.','err')});
    }
    quill.on('text-change',function(_d,_o,src){if(src==='user')touched()});
    $$('#title,#subtitle,#excerpt,#sources_md').forEach(function(el){el.addEventListener('input',touched)});
    form.addEventListener('submit',function(){ta.value=body();dirty=false;clearTimeout(timer)});
    window.addEventListener('beforeunload',function(e){if(dirty&&!locked){e.preventDefault();e.returnValue=''}});
    window.__nbrEditor=quill;
  }
  // SEO character counters
  $$('[data-count]').forEach(function(i){var out=i.parentNode.querySelector('.count'),max=parseInt(i.getAttribute('data-count'),10);
    function u(){out.textContent=i.value.length+' characters \u00b7 aim for \u2264 '+max}i.addEventListener('input',u);u()});
})();
