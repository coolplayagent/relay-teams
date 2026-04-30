# The Playbook For Building An AI Native Company

> **Speaker:** Diana Hu, YC Partner
> **Source:** [YouTube - Y Combinator](https://www.youtube.com/watch?v=EN7frwQIbKc)
> **Series:** Startup School

---

## Transcript

**[00:09]**

Hi, I'm Diana and I'm a partner at YC. Over the past few months, it's become clear to me that AI is not just going to change how quickly software gets built or what workflows get automated. It's going to fundamentally change the way startup should be run from what roles will exist to what products are possible to build. In this episode, I'm going to discuss how founders should think about building an AI native company, what roles their team should have, and what concrete internal practices they can adopt right now to move much faster.

**[00:40]**

Currently, most people talk about AI in terms of productivity. They'll talk at length about how it can make engineers more productive or say we need to add copilot to existing workflows and ship more features. This framing misses the shift we're currently seeing, which is less about productivity boosts than entirely new capabilities. The right person with AI tools can now build features that used to require an entire team or were just impossible. Thinking about AI in terms of new capabilities

**[01:11]**

has several implications for how founders should run their companies. At a high level, the way to think about AI is that it should not be a tool your company just uses. It should be the operating system your company runs on. Every workflow, every decision and every process should flow through an intelligent layer that is constantly learning and improving. What this means concretely is every important process in your company should be captured by an intelligent close loop. A close loop

**[01:41]**

captures information, feeds it back into an intelligent systems, and improves the process over time. If you've ever studied control systems, you'll be familiar with the difference between an open loop and a closed loop system. Open loops are controlled systems without feedback loops. In the old world, companies basically ran as open loops. You made a decision, executed it, and didn't always systematically measure the outcome and adjust the process. Open loops are inherently lossy. A closed loop, on the other hand, is self-regulating. It continuously

**[02:14]**

monitors its output and adjusts its process to better meet the stated goal. Closed loops are extremely powerful for correctness and stability. With self-improving agents, your company should run as a closed loop. To build these closed loops, you will need to make your entire company queryable. In other words, the whole organization should be legible to AI. Every important action should produce an artifact that the intelligence at the center of the company can learn from and use to self-improve. This means recording your

**[02:45]**

meetings with the AI notetaker, minimizing DMs and emails, and embedding agents throughout communication of all channels. It also means building custom dashboards with everything in the company, revenue, sales, engineering, hiring, ops, everything. Here's a concrete example of how it could work. Take engineering, management, and sprint planning. If you have an agent that has access to your linear tickets, all your Slack engineering channels, all customer feedback from emails or tools like Pylon and GitHub, highle plans in a notion or

**[03:16]**

Google doc, sales calls and recordings from daily standups, then the agent can analyze what was actually shipped in your previous sprint and how well they met customers needs for real. From there, you can go a step further with full visibility into what shipped, what worked, and what didn't. Agents can start looking ahead. They can propose sprint plans for engineers that are way more predictable and accurate and on track. The days of anch manager status rollups that are super lossy are gone.

**[03:47]**

Having managed engineering teams myself and now seeing this across multiple YC companies, this is a gamecher. What used to require constant coordination becomes legible and querable by default. I've seen teams that do this cut their engineering sprint time in half and get close to 10x more done in that time. The overarching principle here is that to get their full capabilities, you need to provide models with as much context as you would provide an employee. When you

**[04:18]**

do this, your company stops operating as a open loop where information is fragmented and manually interpreted. It becomes instead a closed loop system. Status, decisions, and outcomes are continuously captured and fed back into this intelligence layer. The result is a system that always has an upto-date view of what's actually happening. There's also a new paradigm emerging for how the highest velocity companies build product AI software factories. If you're

**[04:48]**

familiar with the testdriven development or TDD, this is the next evolution of that. With software factories, humans write a spec and a set of tests that define success and then AI agents generate the implementation and code and iterate until the test pass. The human defines what to build and judges the output. The actual code is the agent's job. Some companies have already pushed this to the point where their repos contain no handwritten code,

**[05:21]**

just specs and test harnesses. Strong DM's AI team is an example of how to do this. Their end goal was a system that essentially eliminated the need for a human to write or review code. And so they built their own software factory where specs and a scenario based validations drive agents to write tests and iterate on code until it meets a probabilistic satisfaction threshold and it works. This is how you achieve the thousandx engineer that Steve Jay talks

**[05:53]**

about by surrounding a single engineer with a system of agents that enable them to build things they would have never been able to build before. The era of the thousand or even 10,000 X engineer is here. One implication of building your company this way with AI loops everywhere, a queryable organization and software factories is that the classic management hierarchy no longer makes sense. In the old world, you needed middle managers and coordinators to route information inefficiently up and

**[06:25]**

down an organization. In the new world, the intelligence layer serves that purpose. If your company is queryable, artifactrich, and legible to an AI, you should have almost no human middleware. This matters because your company's velocities is only as fast as its information flow. Every layer of human routing you can remove is the direct speed gain. A great example is what Jack Dorsey is doing over at Block. After

**[06:56]**

going deep on the tools, he's come to the same conclusion many have. This is about more than just incremental productivity gains. His view is that if you keep the same org chart and management structure, you've missed the shift entirely. The company itself has to be rebuilt as an intelligence layer with humans at the edge guiding it rather than routing information through it. Going forward, Jack suggests every company will have three employee archetypes. The first is the individual contributor or IC basically the builder

**[07:28]**

operator. This is someone who directly makes and runs things in an AI native company. This is not limited to engineers. Everyone builds and ops support sales. Everyone comes to meetings with working prototypes not pitch decks. Second is the DRRI the directly responsible individual focused on strategy and customer outcomes. This is not a classic manager is the person with a clear responsibility for the result. One person, one outcome, no

**[07:59]**

hiding. The third is the AI founder type. This person still builds, still coaches and leads by example. If you're the founder, this needs to be you at the forefront, showing your team what massive capability gains look like, not delegating your AI strategy to someone else. With this structure, companies will be able to get outsized results with much smaller teams. Maximizing token usage, not headcount, will be the critical shift. The best companies will be the ones that are token maxing. Think

**[08:30]**

of the trade-off this way. One person with AI tools can be the equivalent of what used to take a large engineering team at a preAI company. That means dramatically leaner engineering, design, HR, and admin teams. And so you should be willing to run an uncomfortably high API bill because it's replacing what would have taken a far more expensive and inflated headcount. But don't just take my word for any of this. You cannot outsource your conviction on the power

**[09:02]**

of these tools. You need to develop it yourself by actually sitting with coding agents and using them until you start to break your own priors about what is now possible to build. If you are an early stage founder, you have a huge advantage in getting ahead on this. You don't have legacy systems in orchards or thousands of people to retrain. You are small enough to build your company right from day one. The opposite is the case for existing companies. They have to maintain and grow a live product while

**[09:32]**

unwinding years of standard operating procedures and core assumptions about how software gets built. Some companies can achieve this by spinning up small internal skunk work teams that can build AI native systems from scratch separate from the core business. Mutiny is a great example of this. But for most, every change to their core processes risk breaking something that already works. So by their nature, these large companies will have a much harder time going AI native. Startups don't have

**[10:03]**

that constraint and that's a major edge to take advantage of. You can design your systems, workflows and culture around AI from the start and as a result operate thousand times faster than the incumbents. Same
