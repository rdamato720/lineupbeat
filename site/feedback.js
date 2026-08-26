(function(){
  'use strict';
  var API='https://feedback.lineupbeat.com/feedback';
  var css=document.createElement('link');css.rel='stylesheet';css.href='/feedback.css';document.head.appendChild(css);
  var wrap=document.createElement('div');wrap.className='lb-feedback';wrap.innerHTML='\
<button class="lb-feedback-tab" type="button" aria-haspopup="dialog">Help us improve</button>\
<dialog class="lb-feedback-dialog" aria-labelledby="lb-feedback-title">\
 <form method="dialog" class="lb-feedback-close-form"><button class="lb-feedback-close" aria-label="Close feedback form">×</button></form>\
 <form class="lb-feedback-form">\
  <p class="lb-feedback-kicker">READER FEEDBACK</p><h2 id="lb-feedback-title">Help us make Lineup Beat better.</h2>\
  <p class="lb-feedback-lede">Tell us what is useful, what is confusing or what we should build next.</p>\
  <fieldset><legend>What kind of feedback?</legend><div class="lb-feedback-types">\
   <label><input type="radio" name="category" value="ERROR" required><span>Something is wrong</span></label>\
   <label><input type="radio" name="category" value="FEATURE"><span>Suggest a feature</span></label>\
   <label><input type="radio" name="category" value="GENERAL"><span>General feedback</span></label>\
  </div></fieldset>\
  <label class="lb-feedback-label">Your feedback<textarea name="message" minlength="10" maxlength="2000" required placeholder="Tell us what you noticed…"></textarea></label>\
  <label class="lb-feedback-label">Email <small>(optional — only used to follow up)</small><input name="email" type="email" maxlength="254" autocomplete="email" placeholder="you@example.com"></label>\
  <label class="lb-feedback-hp" aria-hidden="true">Website<input name="website" tabindex="-1" autocomplete="off"></label>\
  <p class="lb-feedback-status" role="status" aria-live="polite"></p>\
  <button class="lb-feedback-submit" type="submit">Send feedback <span aria-hidden="true">→</span></button>\
 </form>\
</dialog>';
  document.body.appendChild(wrap);
  var dialog=wrap.querySelector('dialog'),form=wrap.querySelector('.lb-feedback-form');
  wrap.querySelector('.lb-feedback-tab').addEventListener('click',function(){dialog.showModal();});
  form.addEventListener('submit',async function(event){
    event.preventDefault();var button=form.querySelector('.lb-feedback-submit'),status=form.querySelector('.lb-feedback-status');
    button.disabled=true;status.textContent='Sending…';var data=Object.fromEntries(new FormData(form));data.page_url=location.href;
    try{var response=await fetch(API,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});var result=await response.json();
      if(!response.ok)throw new Error(result.error||'Unable to send feedback.');
      form.reset();status.textContent='Thanks — your feedback goes directly to the Lineup Beat team.';button.textContent='Feedback sent ✓';
      setTimeout(function(){dialog.close();button.disabled=false;button.innerHTML='Send feedback <span aria-hidden="true">→</span>';status.textContent='';},1800);
    }catch(error){status.textContent=error.message||'Unable to send feedback. Please try again.';button.disabled=false;}
  });
})();
