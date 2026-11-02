/* ElCinema source day-boundary probe; counts/UTC times ONLY, no programmes. */
const axios = require('axios')
const cheerio = require('cheerio')
const dayjs = require('dayjs')
require('dayjs/locale/ar')
require('dayjs/locale/en')
const cfg = require('./sites/elcinema.com/elcinema.com.config.js')
const targets = [
  { site_id: '1136', id: 'AbuDhabiTV.ae@SD' },
  { site_id: '1382', id: 'AlArabyTV2.qa@SD' }
]
const dates = ['2026-10-17','2026-10-18','2026-10-19']
function section($, date, lang) {
  const marker = dayjs(date).locale(lang).format('dddd D')
  const d = $('.dates').filter((i, el) =>
    $(el).text().trim().replace(/\s\s+/g, ' ').includes(marker)
  ).first()
  const parent = d.parent().next()
  return { found: !!d.length, cards: $('.padded-half', parent).length }
}
;(async function(){
  for (const ch of targets) {
    const lang = 'ar'
    let html
    const url = 'https://elcinema.com/tvguide/' + ch.site_id + '/'
    try {
      const reply = await axios.get(url, {timeout:22000,maxContentLength:1500000,maxBodyLength:1500000,
        headers: cfg.request.headers, maxRedirects:0 })
      if (reply.status!==200 || typeof reply.data !== 'string') throw new Error('UNEXPECTED_RESPONSE')
      html=reply.data
    } catch(err) {
      console.log('SOURCE_CHECK_UNAVAILABLE',JSON.stringify({id:ch.id,kind:err.code||err.name}))
      continue
    }
    const $=cheerio.load(html)
    let info={id:ch.id,http:200,bytes:Buffer.byteLength(html),headingCount:$('.dates').length,days:[]}
    for (const date of dates) {
      const slot=section($,date,lang)
      const programmes=cfg.parser({content:html,channel:{site_id:ch.site_id,lang},date:dayjs(date)})
      const valid=programmes.filter(x=>x.start&&x.stop&&x.start.isValid()&&x.stop.isValid())
      let inversions=0, prev=null, maxBackwardMinutes=0
      for(const p of valid) {
        let epoch=p.start.valueOf()
        if(prev!==null && epoch < prev){
          inversions++
          maxBackwardMinutes=Math.max(maxBackwardMinutes,Math.round((prev-epoch)/60000))
        }
        prev=epoch
      }
      info.days.push({date,header_found:slot.found,source_html_cards:slot.cards,
        adapter_items:programmes.length,valid_time_pairs:valid.length,
        time_order_inversions:inversions,max_backwards_minutes:maxBackwardMinutes,
        earliest_start_utc:valid.length?dayjs(Math.min(...valid.map(x=>x.start.valueOf()))).toISOString():null,
        latest_stop_utc:valid.length?dayjs(Math.max(...valid.map(x=>x.stop.valueOf()))).toISOString():null
      })
    }
    console.log('ELCINEMA_DAY_BOUNDARY_METADATA',JSON.stringify(info))
  }
})().catch(e=>{console.error('PROBE_ERROR_TYPE',e.name);process.exitCode=1})
