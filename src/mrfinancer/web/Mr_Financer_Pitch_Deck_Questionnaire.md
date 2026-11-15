# Mr Financer Pitch Deck Questionnaire

Choose an audience, a slide count from 6 to 25, and a format: live presentation, leave-behind, or both. Add the speaking time if it has been assigned. Treat cover and appendix slides as part of the count.

For each answer, write a line or two. For figures, include currency or unit, period, and source. Select **Founder-reported**, **Documented**, **Estimate**, **Target**, or **I don’t know**. A documented answer needs a shareable source link. Check whether clients, team photos, and source documents can be shared with the intended audience.

| Slide count | What fits |
| --- | --- |
| 6 | One slide each for company, problem and product, market and competition, traction and business model, team and financials, and the ask. |
| 10–12 | Separate the paired topics and use remaining slides for the strongest proof, such as a demo or why now. |
| 15–20 | Add evidence, unit economics, go-to-market, operations, risks, milestones, and use of funds where supported. |
| 21–25 | Make room for expansion, downside scenarios, distribution partners, and diligence references. |

The Business plan section can bring forward founder answers, selected competitor figures, and calculated forecast values. Keep each figure's status and source in the deck. If the audience has asked for a specific length, reduce word count on combined slides and use note space for detail.

## Cover and context

### Company in one sentence

- What is the company or project name? **Core**
- Describe what you sell, who it serves, and the main outcome in one sentence. **Core**
- What public website or contact should appear on the deck?

### Why now

- What changed recently in technology, buyer behavior, cost, or regulation?
- Why does that change make this the right time to build or buy?

### Long-term vision

- If this works, what changes for customers in three to five years?
- What adjacent markets or uses could follow, and what must be true first?

## Problem and product

### Customer problem

- Who uses the product, who pays for it, and who approves the purchase? **Core**
- What specific job is hard today? How often does it occur and what does it cost? **Core**
- What do customers do now? What workaround have you observed firsthand?

### Product and value

- What are the main steps from starting to getting the result? **Core**
- What measurable improvement can you show or test? Give the baseline, target, unit and date if known.
- Which capabilities are available today, planned, or still experimental?

### Product demonstration

- Which screenshot, prototype, video or sample can recipients open?
- Which moment in the demo shows the customer outcome?
- Is the visual a live product, prototype, mockup or illustrative concept?

### Customer evidence

- How many buyer interviews or pilots did you complete, over what period, and what did they validate?
- What named customer or quote may you share, and what result can you substantiate?

## Market and business model

### Market opportunity

- Which customer segment and geography come first? **Core**
- How many eligible buyers can you identify? Give the geographic scope and method.
- What annual spend per qualifying buyer did you measure or assume? Include currency and unit.
- What share is reachable through your initial channels and over what period?

### Competition and differentiation

- Name the direct competitors and indirect substitutes buyers actually consider.
- On which two buying criteria are you stronger or weaker? Give evidence and known price differences.

### How the business makes money

- Who pays, for what unit, and how often? **Core**
- What are the current or proposed prices, tiers and currencies?
- How are contracts renewed, and how long does a sale usually take?

### Unit economics

- What is the delivery cost per unit, including variable fees?
- What does it cost to acquire a paying customer, and how was that calculated?
- What retention, repeat purchase or payback data do you have? Include cohort and period.

### Defensibility

- What durable advantage do you own or control today?
- Which partners, suppliers, data rights, or technology dependencies could weaken it?

### Go-to-market

- Which sales channel will reach your first customers, and how does a lead become a sale?
- What channel test, budget, target and decision date have you set?
- How many leads accepted a demo or converted into paid customers in the measured period?

## Evidence, team and execution

### Traction and progress

- What stage have you reached and when?
- List paid customers, revenue, growth, usage or retention. Give a period, units and source for each figure.
- If pre-launch, what observable proof do you have, such as a paid pilot, waitlist, or signed letter of intent?

### Team and ownership

- Who are the founders and leads? For each, give their role and relevant experience.
- Who owns product, delivery, sales and finance? Which roles are open?

### Delivery and operations

- How do you deliver to a customer, and where does the process slow down?
- What can the current team deliver per month? Include resources and the main constraint.

### Roadmap and milestones

- What will you finish in the next ninety days? Include owners, dates and measurable outcomes.
- Which milestones matter over the next twelve to eighteen months and what unlocks each?

### Risks and mitigations

- What could go wrong with demand, costs, distribution, compliance or supply?
- Which action, owner and trigger will you use to manage each material risk?

### Partners and distribution proof

- Which partners are signed, piloting, in discussion, or targets? Which can be named?
- What does each partner provide and what proof or milestone is available?

## Financials and the ask

### Financial outlook

- What currency and period does the forecast cover?
- Which assumptions most affect revenue, margin and cash? Give base and downside figures if available.
- How much cash is available and when could it run low? Include the model date.

### The ask and next step

- What exact action should this audience take? For funding, include amount, currency and instrument if known.
- What decision date, meeting, pilot, or follow-up should recipients agree to?

### Use of funds

- How will funds be allocated? Give amounts or percentages and the milestone each item buys.
- What runway would it buy, under which assumptions?
- Which funds are committed or still being discussed?

### Downside scenario

- Which assumptions change in a downside case, and how do cash and delivery respond?
- What expenses or timing could change, and at what trigger?

### Sources and diligence

- Which investor-accessible links support the main numbers? Give the metric, period and document owner.
- Which inputs are estimates or unknown, and what will you validate next?

### Closing message

- What single idea should the audience remember?
- What public contact or landing page can recipients use?
## Visuals and image generation

Enter a short public visual brief, such as “a small bakery packing subscription boxes.” Choose an image style. Each selected illustration slide gets a prompt based on that brief and the slide topic. You can edit the exported prompts. Use authentic product screenshots, approved logos and team photos where the visual is evidence. Build charts and financial tables from reviewed figures.

- **FLUX.2 Klein 4B** is the first choice and is already in the `image-generator-marketer` bucket. The bucket has a Diffusers pipeline. The official model is Apache 2.0 and supports text-to-image and reference editing. [Model card](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B)
- **FLUX 3 Image Klein** is in `flux-3-image-klein-bucket`, including FP8 and NVFP4 variants. It is a conditional alternative for multi-reference composition under the FLUX Kommunity License. [Model card and license](https://huggingface.co/black-forest-labs/flux-3-image-klein)
- **Qwen-Image-Edit-2511** files are in `flux2-dev`. Consider it for approved reference-image editing. Test the existing ComfyUI-format bundle separately for memory use. [Model card](https://huggingface.co/Qwen/Qwen-Image-Edit-2511)
- **FLUX.2 dev** is also present in `flux2-dev`. Production or commercial use of the self-hosted checkpoint requires the appropriate license. [Black Forest Labs licensing](https://help.bfl.ai/articles/9272590838-self-serve-dev-license-overview-pricing)

In Mr. Financer, build the outline, enter your Hugging Face access token, then open **Slide illustration** to generate and download a wide image. The Space receives the token on job submission and result retrieval, and generation uses your ZeroGPU quota. [ZeroGPU](https://huggingface.co/docs/hub/spaces-zerogpu) · [Spaces API and quota attribution](https://huggingface.co/docs/hub/spaces-api-endpoints)

Prompt templates cover the first version. **Qwen3.5-2B** is an optional Apache-2.0 prompt editor if you need variants by sector or visual direction. A small CPU deployment can process only the approved visual brief and style fields. [Model card](https://huggingface.co/Qwen/Qwen3.5-2B)

## Pitch references

- [Y Combinator seed pitch deck guide](https://www.ycombinator.com/library/2u-how-to-build-your-seed-round-pitch-deck)
- [Sequoia Capital business plan guidance](https://sequoiacap.com/article/writing-a-business-plan/)

