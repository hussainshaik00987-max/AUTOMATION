import * as store from './store.js'
import { config } from './config.js'

// ---------------------------------------------------------------------------
// Everything a lead reads on WhatsApp.
//
// Written in Roman Hinglish, not Devanagari, because that is how Indian business
// owners actually type and read on WhatsApp — Devanagari in a business chat reads
// as a bulk/marketing template. English variants are used when the CALL switched
// to English (the agent reports which via set_language), so nobody gets a message
// in a language they told us on the phone they don't speak.
//
// The voice is मीरा / Meera, female, Sharma IT Solution — the same person who
// just rang them (see agent-openai/pitch_hi.go). Every verb about herself is
// feminine: "bol rahi hoon", "bhej rahi hoon". A lead who spoke to a woman thirty
// minutes ago and then hears from a man has been handed the one detail that turns
// a warm follow-up into an obvious machine.
//
// ── Why thirty of each ────────────────────────────────────────────────────────
// Identical text sent to hundreds of numbers is among the strongest signals
// WhatsApp uses to flag an account, and it is the first thing two shop owners
// notice when they compare phones. Three variants was not enough: at three, the
// fourth lead of the morning is already reading something a neighbour got.
//
// So the banks below hold thirty each, and they vary on the axes a person varies
// on — length above all (one line to three short paragraphs), greeting, whether
// there is an emoji, whether the business is named, whether the ask is a question
// or a statement. Nothing here is assembled from interchangeable fragments;
// generated permutations read like generated permutations.
//
// ── § ─────────────────────────────────────────────────────────────────────────
// '§' marks a natural seam: the point where a person would have hit send and
// carried on typing. bubbles() decides per message whether to actually break
// there or close it up into one message, so the same template lands sometimes as
// one bubble and sometimes as two.
// ---------------------------------------------------------------------------

const SEAM = '§'

const firstName = (name) => (name || '').trim().split(/\s+/)[0] || ''

// ---------------------------------------------------------------------------
// The ping — short, personal, asks nothing except permission.
//
// Deliberately contains NO link. An unsolicited link as the first message to a
// number that has never messaged you is the fastest route to a report and a ban.
// ---------------------------------------------------------------------------

const PING_HI = [
  (c) => `Namaste${c.sir} 🙏 Meera bol rahi hoon Sharma IT Solution se — abhi call pe baat hui thi.${SEAM}Yahan link bhej doon?`,
  (c) => `Hi${c.sir}, Sharma IT se Meera. Abhi thodi der pehle call kiya tha aapko.${SEAM}${c.business} ki demo website ready hai — bhej doon yahan?`,
  (c) => `Namaste ji 🙏${SEAM}Meera bol rahi hoon, abhi aapse baat hui thi. Jo demo website banayi hai ${c.business} ke liye, uska link bhej doon?`,
  (c) => `${c.hi}Meera bol rahi hoon Sharma IT Solution se 🙏 Abhi call pe baat hui thi na — link bhej doon?`,
  (c) => `Namaste${c.sir}. Meera, Sharma IT Solution.\n\nAbhi phone pe ${c.business} ke baare mein baat hui thi. Demo website taiyaar hai.${SEAM}Bhej doon?`,
  (c) => `${c.hi}abhi call kiya tha — Meera, Sharma IT se 🙏 Demo ka link yahin bhej doon?`,
  (c) => `Namaste${c.sir} 🙏\n\nMeera bol rahi hoon Sharma IT Solution se. Jaisa call pe kaha tha, ${c.business} ke liye ek demo website banayi hai.${SEAM}Link bhej doon yahan?`,
  (c) => `Hello${c.sir}, Meera here — Sharma IT Solution. Abhi baat hui thi aapse.${SEAM}Website ka demo ready hai, bhej doon?`,
  (c) => `${c.hi}main Meera, abhi call pe baat hui thi 🙏${SEAM}${c.business} ki demo site ban gayi hai. Link yahin bhej doon?`,
  (c) => `Namaste${c.sir}, Meera bol rahi hoon Sharma IT se.\n\nAapne kaha tha WhatsApp pe bhej dena — bhej doon abhi?`,
  (c) => `Namaste${c.sir} 🙏 Meera bol rahi hoon Sharma IT Solution se.\n\nAbhi thodi der pehle call kiya tha — ${c.business} ke liye jo free demo website banayi hai, wo taiyaar hai.${SEAM}Ek baar dekh lijiyega, 1 min lagega. Bhej doon?`,
  (c) => `Hi${c.sir} 🙏 Sharma IT Solution se Meera. Call pe baat hui thi abhi.${SEAM}Demo bhej doon yahan?`,
  (c) => `${c.hi}namaste 🙏 Meera bol rahi hoon Sharma IT Solution se — abhi aapko call kiya tha ${c.business} ke liye.${SEAM}Demo website ka link bhej doon?`,
  (c) => `Namaste${c.sir}. Abhi call pe baat hui thi — Meera, Sharma IT se.\n\nScreenshot aur link dono bhej sakti hoon. Bhej doon?`,
  (c) => `${c.hi}Meera bol rahi hoon Sharma IT Solution se 🙏\n\nAapki demo website ready hai. Yahan bhej doon?`,
  (c) => `Namaste ji, main Meera Sharma IT se — abhi phone pe baat hui thi.${SEAM}${c.business} ke liye jo site banayi hai, ek baar dekh lijiyega? Link bhej doon.`,
  (c) => `Hi${c.sir}, abhi call kiya tha — Sharma IT Solution, Meera bol rahi hoon.${SEAM}Aapke liye demo taiyaar hai. Bhej doon yahan?`,
  (c) => `Namaste${c.sir} 🙏\n\nSharma IT Solution se Meera. Abhi baat hui thi aapse ${c.business} ke baare mein.${SEAM}Demo website ka link yahin bhej doon?`,
  (c) => `${c.hi}Meera bol rahi hoon, Sharma IT se 🙏 Abhi call hui thi. Demo bhej doon?`,
  (c) => `Namaste${c.sir}, Meera bol rahi hoon.\n\nAbhi Sharma IT Solution se call kiya tha aapko. Jo demo website aapke liye banayi hai, wo bhej doon yahan?`,
  (c) => `Hello ji 🙏 Sharma IT se Meera bol rahi hoon — abhi baat hui thi.${SEAM}${c.business} ki demo site ready hai, link bhej doon?`,
  (c) => `${c.hi}abhi phone pe baat hui thi na — Meera, Sharma IT Solution.\n\nDemo website bana di hai aapke liye, bilkul free. Bhej doon?`,
  (c) => `Namaste${c.sir} 🙏 Meera, Sharma IT Solution.${SEAM}Aapka demo ready hai — link bhej doon yahan?`,
  (c) => `Hi${c.sir}, Sharma IT Solution se Meera bol rahi hoon.\n\nAbhi call pe aapne kaha tha WhatsApp pe bhej dena. Bhej doon abhi?`,
  (c) => `Namaste${c.sir} 🙏\n\nMeera bol rahi hoon Sharma IT Solution se — abhi thodi der pehle aapse baat hui thi.\n\n${c.business} ke liye maine ek demo website banayi hai. Bilkul free hai, sirf dikhane ke liye.${SEAM}Link bhej doon yahan?`,
  (c) => `${c.hi}namaste 🙏 abhi call pe baat hui thi — Meera, Sharma IT se. Demo link bhej doon?`,
  (c) => `Namaste${c.sir}. Main Meera, Sharma IT Solution se.${SEAM}Aapke business ki demo website taiyaar hai. Yahan bhej doon dekhne ke liye?`,
  (c) => `Hi${c.sir} — Meera from Sharma IT. Abhi call hui thi na.${SEAM}${c.business} ka demo ready hai. Bhej doon?`,
  (c) => `Namaste${c.sir} 🙏 Sharma IT Solution se Meera bol rahi hoon.\n\nAbhi baat hui thi — demo website ban gayi hai aapke liye.${SEAM}Link bhej doon, ek baar dekh lijiyega?`,
  (c) => `${c.hi}Meera bol rahi hoon Sharma IT se 🙏 Abhi call kiya tha ${c.business} ke liye.\n\nDemo taiyaar hai — bhej doon yahan?`,
]

const PING_EN = [
  (c) => `Hello${c.sir} 🙏 This is Meera from Sharma IT Solution — we just spoke on the phone.${SEAM}Shall I send you the link here?`,
  (c) => `Hi${c.sir}, Meera from Sharma IT here — we spoke a little while ago.${SEAM}The demo website for ${c.business} is ready. Can I send the link?`,
  (c) => `Hello ji 🙏${SEAM}Meera from Sharma IT Solution, we just spoke. Shall I share the demo website link here?`,
  (c) => `${c.hi}this is Meera from Sharma IT — we were on a call just now. Shall I send the demo link?`,
  (c) => `Hello${c.sir}. Meera, Sharma IT Solution.\n\nWe spoke about ${c.business} a few minutes ago. The demo website is ready.${SEAM}Shall I send it?`,
  (c) => `${c.hi}Meera from Sharma IT Solution 🙏 I called you a short while ago — may I send the demo link here?`,
  (c) => `Hello${c.sir} 🙏\n\nThis is Meera from Sharma IT Solution. As I mentioned on the call, I've built a demo website for ${c.business}.${SEAM}Shall I send the link?`,
  (c) => `Hi${c.sir}, Meera here from Sharma IT. We just spoke.${SEAM}Your demo site is ready — shall I send it across?`,
  (c) => `${c.hi}it's Meera, we spoke on the phone a moment ago 🙏${SEAM}The demo for ${c.business} is done. Shall I send the link here?`,
  (c) => `Hello${c.sir}, Meera from Sharma IT.\n\nYou said to send it on WhatsApp — shall I send it now?`,
  (c) => `Hello${c.sir} 🙏 This is Meera from Sharma IT Solution.\n\nI called you a little while ago — the free demo website I built for ${c.business} is ready.${SEAM}It takes a minute to look through. Shall I send it?`,
  (c) => `Hi${c.sir} 🙏 Meera from Sharma IT Solution. We were just on a call.${SEAM}Shall I send the demo here?`,
  (c) => `${c.hi}Meera here from Sharma IT Solution 🙏 I called you about ${c.business} just now.${SEAM}May I send the demo website link?`,
  (c) => `Hello${c.sir}. We spoke a few minutes ago — Meera, from Sharma IT.\n\nI can send a screenshot as well as the link. Shall I?`,
  (c) => `${c.hi}this is Meera from Sharma IT Solution 🙏\n\nYour demo website is ready. Shall I send it here?`,
  (c) => `Hello ji, Meera from Sharma IT — we spoke on the phone.${SEAM}Would you like to see the site I built for ${c.business}? I'll send the link.`,
  (c) => `Hi${c.sir}, I called a short while ago — Meera, Sharma IT Solution.${SEAM}Your demo is ready. Shall I send it here?`,
  (c) => `Hello${c.sir} 🙏\n\nMeera from Sharma IT Solution. We spoke about ${c.business} a few minutes ago.${SEAM}Shall I send the demo website link here?`,
  (c) => `${c.hi}Meera from Sharma IT 🙏 We just spoke — shall I send the demo?`,
  (c) => `Hello${c.sir}, this is Meera.\n\nI called you from Sharma IT Solution a short while ago. Shall I send the demo website I built for you?`,
  (c) => `Hello ji 🙏 Meera from Sharma IT here — we spoke just now.${SEAM}The demo site for ${c.business} is ready. Shall I send the link?`,
  (c) => `${c.hi}we were on a call a moment ago — Meera, Sharma IT Solution.\n\nI've built you a demo website, completely free. Shall I send it?`,
  (c) => `Hello${c.sir} 🙏 Meera, Sharma IT Solution.${SEAM}Your demo is ready — shall I send the link here?`,
  (c) => `Hi${c.sir}, Meera from Sharma IT Solution.\n\nOn the call you said to send it on WhatsApp. Shall I send it now?`,
  (c) => `Hello${c.sir} 🙏\n\nThis is Meera from Sharma IT Solution — we spoke a little while ago.\n\nI've built a demo website for ${c.business}. It's free, just to show you what it would look like.${SEAM}Shall I send the link here?`,
  (c) => `${c.hi}we spoke on the phone just now — Meera, from Sharma IT 🙏 Shall I send the demo link?`,
  (c) => `Hello${c.sir}. This is Meera, Sharma IT Solution.${SEAM}The demo website for your business is ready. Shall I send it here for you to look at?`,
  (c) => `Hi${c.sir} — Meera from Sharma IT. We spoke just now.${SEAM}The demo for ${c.business} is ready. Shall I send it?`,
  (c) => `Hello${c.sir} 🙏 Meera from Sharma IT Solution here.\n\nWe spoke a moment ago — your demo website is built.${SEAM}Shall I send the link so you can have a look?`,
  (c) => `${c.hi}Meera from Sharma IT 🙏 I called you about ${c.business} just now.\n\nThe demo is ready — shall I send it here?`,
]

// ---------------------------------------------------------------------------
// The payload — the link, plus the one line that does the actual selling: this
// was made for them specifically.
//
// The URL always sits in the LAST segment. When a screenshot exists it becomes
// that segment's caption, so the picture and the link arrive together as one
// message rather than as a two-message burst. linkMessage() enforces this.
// ---------------------------------------------------------------------------

const LINK_HI = [
  (c) => `Ye lijiye${c.sir} 👇${SEAM}${c.url}\n\nMaine ye demo specially ${c.business} ke liye banayi hai — aapke kaam aur reviews ke hisaab se. Mobile pe khol ke ek baar dekh lijiye.\n\nPasand aaye toh batayiega, isme aapke apne photos aur details daal denge. 🙂`,
  (c) => `${c.hi}ye rahi aapki demo website 👇${SEAM}${c.url}\n\nSpecially ${c.business} ke liye banayi hai. Phone pe khulegi, 1 min lagega dekhne me.\n\nSahi lage toh bata dijiyega — aapke photos daal ke live kar denge.`,
  (c) => `Link ye raha${c.sir} 👇${SEAM}${c.url}\n\n${c.business} ke liye maine ye demo banayi hai — bilkul free, sirf dekhne ke liye. Ek baar khol ke dekhiye.\n\nKuch change karwana ho toh bta dijiyega. 🙂`,
  (c) => `${c.url}\n\nYe raha${c.sir} — ${c.business} ki demo website. Ek baar khol ke dekh lijiye. 🙂`,
  (c) => `Bhej rahi hoon${c.sir} 👇${SEAM}${c.url}\n\nAapke kaam ke hisaab se banayi hai. Mobile pe hi khol lijiyega, achhi dikhegi.`,
  (c) => `${c.hi}ye lijiye 🙂${SEAM}${c.url}\n\nDemo hai — abhi sample photos lagi hain. Aapko theek lage toh aapke apne photos, timing, number sab daal denge.\n\nDekh ke bata dijiyega.`,
  (c) => `Ye aapki demo site hai${c.sir} 👇${SEAM}${c.url}\n\n${c.business} ke naam se banayi hai. Free hai, dekhne ke liye koi charge nahi.`,
  (c) => `${c.url}\n\n${c.hi}ye demo maine aapke liye banayi hai. 1 min me dekh lijiyega — phir batayiega kaisa laga. 🙂`,
  (c) => `Lijiye${c.sir} 👇${SEAM}${c.url}\n\nAapke business ke liye banayi hai ye. Upar photos, neeche contact aur WhatsApp button — client seedha aapko message kar sakta hai.\n\nAur kuch add karna ho toh bol dijiyega.`,
  (c) => `${c.hi}link ye raha 👇${SEAM}${c.url}\n\nMobile pe khol ke dekhiyega. ${c.business} ke liye hi banayi hai — sample hai abhi, aapke photos daalne baaki hain.`,
  (c) => `Ye raha aapka demo${c.sir} 🙂${SEAM}${c.url}\n\nKhol ke dekh lijiye — 1 minute lagega. Pasand aaye toh aage ki baat kar lete hain.`,
  (c) => `${c.url}\n\nYe lijiye${c.sir} — ${c.business} ki demo website. Bilkul free hai. Dekh ke batayiega kaisi lagi. 🙂`,
  (c) => `Bhej di${c.sir} 👇${SEAM}${c.url}\n\nAapke kaam aur reviews dekh ke banayi hai maine. Isme jo bhi change karwana ho — colour, photos, text — sab ho jaayega.`,
  (c) => `${c.hi}ye rahi site 👇${SEAM}${c.url}\n\nEk baar phone pe khol ke dekh lijiye. Achhi lage toh aage badhte hain, warna koi baat nahi. 🙂`,
  (c) => `Ye lijiye 👇${SEAM}${c.url}\n\n${c.business} ke liye banayi hai${c.sir}. Abhi demo hai — live karne me 2-3 din lagte hain, aapke photos aur details ke saath.`,
  (c) => `${c.url}\n\nAapki demo website${c.sir} 🙂 Mobile pe khulegi. Dekh ke bata dijiyega.`,
  (c) => `Link bhej rahi hoon${c.sir} 👇${SEAM}${c.url}\n\nJaisa call pe bataya tha — ye ${c.business} ke liye banayi hai, bilkul free. Dekhne me ek minute lagega.\n\nPasand aaye toh isme aapke apne photos daal denge. 🙂`,
  (c) => `${c.hi}ye lijiye 👇${SEAM}${c.url}\n\nAapke liye banayi hai. Google pe search karne wale client ko aisi hi site dikhni chahiye — number, photos, sab ek jagah.`,
  (c) => `Ye rahi${c.sir} 🙂${SEAM}${c.url}\n\nDemo hai, free hai. Ek baar khol ke dekh lijiye — phir jo change chahiye bata dijiyega.`,
  (c) => `${c.url}\n\n${c.hi}ye demo ${c.business} ke liye banayi hai maine. Khol ke dekhiyega, phir baat karte hain. 🙂`,
  (c) => `Lijiye${c.sir}, ye aapki demo site 👇${SEAM}${c.url}\n\nAbhi jo photos lagi hain wo sample hain — aapke apne photos daal denge. Baaki design aapke kaam ke hisaab se hi hai.`,
  (c) => `${c.hi}bhej rahi hoon 👇${SEAM}${c.url}\n\n1 min nikal ke dekh lijiyega. Kuch bhi change karwana ho toh yahin bata dijiye.`,
  (c) => `Ye lijiye${c.sir} 👇${SEAM}${c.url}\n\nMaine ${c.business} ke liye ye demo banayi hai. Mobile pe khol ke dekhiye — jaise client dekhta hai waise hi dikhegi.\n\nBatayiega kaisi lagi. 🙂`,
  (c) => `${c.url}\n\nYe raha link${c.sir}. Demo hai, dekhne ka koi charge nahi. Pasand aaye toh batayiega. 🙂`,
  (c) => `Ye rahi aapki demo website 👇${SEAM}${c.url}\n\n${c.business} ke naam se banayi hai${c.sir}. Free hai — sirf dikhane ke liye, taaki aap dekh sakein ki online kaisi lagegi.\n\nAage badhna ho toh photos aur details bhej dijiyega, live kar denge.`,
  (c) => `${c.hi}link ye raha 🙂${SEAM}${c.url}\n\nEk baar dekh lijiye. Achhi lage toh aage ki baat karte hain.`,
  (c) => `Bhej rahi hoon 👇${SEAM}${c.url}\n\nAapke liye specially banayi hai${c.sir}. Phone pe khol ke dekhiyega — 1 min ka kaam hai.`,
  (c) => `${c.url}\n\nYe ${c.business} ki demo hai${c.sir} — maine banayi hai aapke kaam dekh ke. Dekh ke bata dijiyega. 🙂`,
  (c) => `Lijiye${c.sir} 👇${SEAM}${c.url}\n\nDemo website hai — abhi sample content hai, aapke photos aur details daalne baaki hain.\n\nDekh ke batayiega, phir aage ka bata deti hoon.`,
  (c) => `${c.hi}ye rahi 👇${SEAM}${c.url}\n\nAapke business ke liye banayi hai. Free demo hai — dekhne ke baad hi aage ki koi baat. 🙂`,
]

const LINK_EN = [
  (c) => `Here you go${c.sir} 👇${SEAM}${c.url}\n\nI built this demo specifically for ${c.business} — based on your work and your reviews. Do open it on your phone, it takes a minute.\n\nIf you like it, let me know and we'll put your own photos and details in. 🙂`,
  (c) => `${c.hi}here's your demo website 👇${SEAM}${c.url}\n\nMade specially for ${c.business}. It opens on mobile, just takes a minute to look through.\n\nLet me know what you think — we can add your own photos and go live.`,
  (c) => `Here's the link${c.sir} 👇${SEAM}${c.url}\n\nThis demo is for ${c.business} — completely free, just to show you. Have a look whenever you get a minute.\n\nHappy to change anything you don't like. 🙂`,
  (c) => `${c.url}\n\nHere it is${c.sir} — the demo site for ${c.business}. Do open it when you get a moment. 🙂`,
  (c) => `Sending it across${c.sir} 👇${SEAM}${c.url}\n\nBuilt around the work you already do. Best viewed on your phone.`,
  (c) => `${c.hi}here you go 🙂${SEAM}${c.url}\n\nIt's a demo, so the photos are samples for now. If it looks right we'll put your own photos, timings and number in.\n\nLet me know what you think.`,
  (c) => `Here's your demo site${c.sir} 👇${SEAM}${c.url}\n\nBuilt under your business name. Free — there's no charge to look.`,
  (c) => `${c.url}\n\n${c.hi}I built this demo for you. A minute is all it takes to look through — tell me what you think. 🙂`,
  (c) => `Here you are${c.sir} 👇${SEAM}${c.url}\n\nThis is for your business. Photos at the top, contact and a WhatsApp button at the bottom — a customer can message you directly from it.\n\nTell me if you'd like anything added.`,
  (c) => `${c.hi}here's the link 👇${SEAM}${c.url}\n\nDo open it on your phone. It's built for ${c.business} — a sample for now, your own photos still to go in.`,
  (c) => `Here's your demo${c.sir} 🙂${SEAM}${c.url}\n\nHave a look, it takes a minute. If you like it we can take it further.`,
  (c) => `${c.url}\n\nHere you go${c.sir} — the demo website for ${c.business}, free to look at. Let me know how you find it. 🙂`,
  (c) => `Sent${c.sir} 👇${SEAM}${c.url}\n\nI put this together after looking at your work and your reviews. Anything you want changed — colours, photos, wording — we can do.`,
  (c) => `${c.hi}here's the site 👇${SEAM}${c.url}\n\nDo open it on your phone. If it looks good we'll take it forward, and if not that's absolutely fine. 🙂`,
  (c) => `Here you go 👇${SEAM}${c.url}\n\nBuilt for ${c.business}, ${c.you}. It's a demo for now — going live takes two or three days once we have your photos and details.`,
  (c) => `${c.url}\n\nYour demo website${c.sir} 🙂 It opens on mobile. Let me know what you think.`,
  (c) => `Sending the link${c.sir} 👇${SEAM}${c.url}\n\nAs I said on the call — built for ${c.business}, completely free. A minute to look through.\n\nIf you like it we'll put your own photos in. 🙂`,
  (c) => `${c.hi}here you go 👇${SEAM}${c.url}\n\nBuilt for you. This is what someone searching on Google should find — your photos, your number, all in one place.`,
  (c) => `Here it is${c.sir} 🙂${SEAM}${c.url}\n\nA demo, and free. Open it up and tell me what you'd like changed.`,
  (c) => `${c.url}\n\n${c.hi}I built this demo for ${c.business}. Have a look and we'll talk. 🙂`,
  (c) => `Here you are${c.sir}, your demo site 👇${SEAM}${c.url}\n\nThe photos on it are samples — we'd swap in your own. The rest is built around the work you actually do.`,
  (c) => `${c.hi}sending it now 👇${SEAM}${c.url}\n\nDo give it a minute when you can. Anything you'd like changed, just tell me here.`,
  (c) => `Here you go${c.sir} 👇${SEAM}${c.url}\n\nI built this demo for ${c.business}. Open it on your phone — that's how your customers will see it.\n\nTell me how you find it. 🙂`,
  (c) => `${c.url}\n\nThere's the link${c.sir}. It's a demo, no charge to look at it. Let me know if you like it. 🙂`,
  (c) => `Here's your demo website 👇${SEAM}${c.url}\n\nBuilt under the ${c.business} name, ${c.you}. It's free — just so you can see how it would look online.\n\nIf you want to go ahead, send your photos and details and we'll make it live.`,
  (c) => `${c.hi}here's the link 🙂${SEAM}${c.url}\n\nHave a look. If it works for you we'll take it from there.`,
  (c) => `Sending it over 👇${SEAM}${c.url}\n\nBuilt specially for you${c.sir}. Open it on your phone — it's a minute's work.`,
  (c) => `${c.url}\n\nThat's the demo for ${c.business}, ${c.you} — I built it after looking at your work. Tell me what you think. 🙂`,
  (c) => `Here you are${c.sir} 👇${SEAM}${c.url}\n\nIt's a demo site — the content is samples for now, your photos and details still to go in.\n\nHave a look and I'll tell you what comes next.`,
  (c) => `${c.hi}here it is 👇${SEAM}${c.url}\n\nBuilt for your business. It's a free demo — nothing to decide until you've seen it. 🙂`,
]

// ---------------------------------------------------------------------------
// Sent when they reply something we can't read as yes or no — a question, a
// "kaun?", a voice note. Reminds them who we are, then the link follows.
// ---------------------------------------------------------------------------

const CLARIFY_HI = [
  (c) => `${c.hi}main Meera, Sharma IT Solution se — abhi aapko call kiya tha ${c.business} ke liye.\n\nAapki ek demo website banayi hai, bilkul free. Bhej rahi hoon 👇`,
  (c) => `Meera bol rahi hoon${c.sir}, Sharma IT Solution se. Abhi thodi der pehle baat hui thi.\n\nJo demo website banayi thi, wo bhej rahi hoon 👇`,
  (c) => `${c.hi}Sharma IT Solution se Meera. Abhi call pe baat hui thi ${c.business} ke baare mein.\n\nDemo website free hai — bhej rahi hoon, dekh lijiyega 👇`,
  (c) => `Namaste${c.sir} 🙏 Meera, Sharma IT se — abhi call kiya tha aapko.\n\nAapke business ki demo website bana di hai. Ye rahi 👇`,
  (c) => `${c.hi}Meera bol rahi hoon Sharma IT Solution se.\n\nAbhi phone pe baat hui thi — maine aapke liye ek free demo website banayi hai. Bhej rahi hoon 👇`,
  (c) => `Ji${c.sir}, Meera — Sharma IT Solution. Abhi baat hui thi.\n\nDemo site ready hai, koi charge nahi. Dekh lijiyega 👇`,
  (c) => `${c.hi}main Sharma IT Solution se Meera bol rahi hoon — abhi call pe baat hui thi na.\n\n${c.business} ke liye demo website banayi hai. Ye rahi 👇`,
  (c) => `Meera${c.sir}, Sharma IT Solution se 🙏 Abhi aapko call kiya tha.\n\nFree demo website banayi hai aapke liye — bhej rahi hoon 👇`,
]

const CLARIFY_EN = [
  (c) => `${c.hi}this is Meera from Sharma IT Solution — I called you a little while ago about ${c.business}.\n\nI've made a free demo website for you. Sending it now 👇`,
  (c) => `This is Meera${c.sir}, from Sharma IT Solution. We spoke a short while ago.\n\nSending the demo website I built 👇`,
  (c) => `${c.hi}Meera from Sharma IT Solution here. We spoke on the phone about ${c.business}.\n\nThe demo website is free — sending it now for you to look at 👇`,
  (c) => `Hello${c.sir} 🙏 Meera from Sharma IT — I called you a moment ago.\n\nI've built a demo website for your business. Here it is 👇`,
  (c) => `${c.hi}this is Meera from Sharma IT Solution.\n\nWe spoke on the phone — I've built you a free demo website. Sending it 👇`,
  (c) => `Yes${c.sir}, Meera here — Sharma IT Solution. We spoke just now.\n\nYour demo site is ready, no charge. Have a look 👇`,
  (c) => `${c.hi}I'm Meera from Sharma IT Solution — we were on a call a short while ago.\n\nI built a demo website for ${c.business}. Here it is 👇`,
  (c) => `Meera${c.sir}, from Sharma IT Solution 🙏 I called you earlier.\n\nI've made you a free demo website — sending it now 👇`,
]

// ---------------------------------------------------------------------------
// Dealing
// ---------------------------------------------------------------------------

// deal picks a template WITHOUT the repetition a plain random pick gives you.
// Math.random() over thirty variants hands out a duplicate inside the first seven
// sends more often than not — the birthday problem, and exactly the coincidence
// that gets noticed when two shop owners on the same street compare phones.
// store.dealFrom() shuffles the bank and deals it one at a time, so all thirty go
// out before any repeats, and the deck survives a restart.
function deal(name, bank, ctx) {
  return bank[store.dealFrom(name, bank.length)](ctx)
}

// bubbles turns a rendered template into the message(s) actually sent. Some of
// the time the seam becomes a real break — a person sends the greeting, then
// types the rest — and the rest of the time it closes up into one message. Same
// template, two different shapes on the wire.
function bubbles(text, splitRate) {
  const parts = text.split(SEAM)
  if (parts.length < 2) return [text.trim()].filter(Boolean)
  if (Math.random() >= splitRate) return [parts.join('\n\n').trim()]
  return parts.map((p) => p.trim()).filter(Boolean)
}

function context(job) {
  const name = firstName(job.ownerName)
  return {
    business: job.business || (isEnglish(job) ? 'your business' : 'aapke business'),
    url: job.demoUrl,
    // "sir" is dropped the moment we know their actual name — a real name is
    // warmer, and stacking "sir" onto a name reads as a script.
    sir: name ? ` ${name} ji` : ' sir',
    you: name ? `${name} ji` : 'sir',
    // Sentence-opening address. Never empty and always capitalised, so a template
    // that starts with it reads correctly whether or not we got a name.
    hi: name ? `${name} ji, ` : 'Sir, ',
  }
}

const isEnglish = (job) => job.language === 'en'

export function pingMessage(job) {
  const ctx = context(job)
  const text = isEnglish(job)
    ? deal('pingEn', PING_EN, ctx)
    : deal('pingHi', PING_HI, ctx)
  // The ping is the FIRST message to a number that has never written to us, and
  // two cold messages in a row is a worse shape than one. Split rarely.
  return bubbles(text, config.splitRatePing)
}

// dropAddress removes a leading "Rakesh ji, " / "Sir, " and re-capitalises what
// is left. Used when a message has just gone out that already addressed them by
// name: nobody says somebody's name twice in two consecutive messages, and it is
// exactly the kind of repetition that reads as two templates stitched together.
function dropAddress(parts) {
  const [first, ...rest] = parts
  const trimmed = first.replace(/^(?:[^\s,]{1,20} ji|Sir), /, '')
  if (trimmed === first) return parts
  return [trimmed.charAt(0).toUpperCase() + trimmed.slice(1), ...rest]
}

export function linkMessage(job, { allowSplit = false, afterIntro = false } = {}) {
  const ctx = context(job)
  const text = isEnglish(job)
    ? deal('linkEn', LINK_EN, ctx)
    : deal('linkHi', LINK_HI, ctx)

  let parts = bubbles(text, allowSplit ? config.splitRateLink : 0)
  if (afterIntro) parts = dropAddress(parts)
  // Belt and braces: the URL has to be in the final segment, because that is the
  // one the screenshot is attached to as a caption. A template edited into the
  // wrong shape collapses back to a single message rather than sending a picture
  // captioned with nothing and a bare link above it.
  if (parts.length > 1 && job.demoUrl && !parts[parts.length - 1].includes(job.demoUrl)) {
    return [parts.join('\n\n')]
  }
  return parts
}

export function clarifyMessage(job) {
  const ctx = context(job)
  return isEnglish(job)
    ? [deal('clarifyEn', CLARIFY_EN, ctx)]
    : [deal('clarifyHi', CLARIFY_HI, ctx)]
}

// Exposed for the self-test in test-flow.js — a bank that silently shrank to one
// variant would be invisible until a hundred leads had read the same sentence.
export const banks = () => ({
  pingHi: PING_HI,
  pingEn: PING_EN,
  linkHi: LINK_HI,
  linkEn: LINK_EN,
  clarifyHi: CLARIFY_HI,
  clarifyEn: CLARIFY_EN,
})
