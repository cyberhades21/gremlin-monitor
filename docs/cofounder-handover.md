# Co-founder handover

For the co-founder, who has about 5 hours a week for this. Written 2026-11-19. If something here is unclear or has changed, ask the engineer before acting on it.

## Start here: the first week

By Friday 27 November 2026:

1. Read this page.
2. Send the written question to Anthropic (job 1). It has the longest wait, and the product's design depends on the answer.
3. Start the search for base repositories (job 2) and for a lawyer (job 3).

## What we are testing

We want to know whether an AI-assisted coding exercise, scored by hidden tests and a written rubric, agrees with what experienced engineers think of the same work. We test that by hand first, before building any product.

In a session, a volunteer engineer gets a small Python codebase with several planted flaws and a coding assistant that we provide. They have about 90 minutes to find and fix the flaws, write down what they found, and describe how the code is organised. Hidden tests and the rubric score the result. Within 48 hours the engineer holds a 15-minute call with the volunteer to check they understand their own work.

The pilot is about 12 sessions with 8 to 10 volunteers. Three or four experienced reviewers read the same sessions without seeing our scores or the volunteer's name, and give their own verdicts. We then compare the two. We call this stage Gate 1a. The target is that our results match the reviewers' on at least 90% of the items the tests can check.

The pilot tests a grading method. It is not a hiring decision, and volunteers are told so.

The engineer runs the sessions by hand on one server. You run everything around the people: recruiting, paperwork, scheduling and messages. The pilot waits on people more than on engineering, so recruiting is the thing most likely to slow us down.

## Words you will meet

| Word | Meaning |
|---|---|
| Planted flaw | A problem we put in a codebase on purpose: a bug, or a document that disagrees with the code. The engineer's specs call these "gremlins" |
| Exercise (also "package") | One problem: the codebase, the brief, the hidden tests and the scoring list |
| Brief | The instructions a volunteer sees. It says how many issues of each kind to look for |
| Hidden tests | Tests the volunteer never sees. They check whether each flaw was really fixed |
| Rubric | Written criteria for what a test cannot check, such as explaining how the code is organised. Each criterion is scored 0 to 3 |
| Session | One volunteer working on one exercise |
| Walkthrough | The 15-minute call after a session |
| Reviewer | An experienced engineer who judges sessions without knowing who did them |
| Gate | A sign-off point. Gate 1a is the end of this manual pilot |

**Words never to use with volunteers:** "gremlin", the name of any base repository, and "Claude Code" as the name of our assistant. Say "the coding assistant". Anthropic's branding rules do not allow us to call our assistant Claude Code, and its real name is not decided.

## Rules that do not bend

1. **No personal data in this repository.** Names, emails and phone numbers of volunteers and reviewers never go in the repo, in a reviewer's packet, or in any message to a reviewer. People are known by codes: volunteers are C-01, C-02 and so on, and reviewers are R1, R2. The only list that links a code to a person is your private sheet, kept outside this repo. Keep it where only you and the engineer can open it.
2. **No session without a signed consent form.** No exceptions, including for friends.
3. **Reviewers never see a name, our results or an experience band.**
4. **Promise nothing.** Do not promise volunteers results, feedback or a job. A report for volunteers has not been designed.

## Your jobs

| # | Job | Needed by | Rough effort |
|---|---|---|---|
| 1 | Send the written question to Anthropic | Fri 27 Nov 2026 | 2 hours |
| 2 | Shortlist five base repositories | Fri 4 Dec 2026 | 4 hours |
| 3 | Find a lawyer for the consent text | Fri 4 Dec 2026 | 1 hour |
| 4 | Draft the consent form, data note, survey and reviewer instructions | Fri 11 Dec 2026 | 8 hours |
| 5 | Recruit 8 to 10 volunteer engineers | First 3 or 4 by Mon 21 Dec 2026 \*, the rest by Mon 11 Jan 2027 | 6 hours |
| 6 | Recruit 3 or 4 reviewers | Mon 4 Jan 2027 \* | 3 hours |
| 7 | Run the logistics | From the first session | About 1 hour per session |

\* These two dates come from the plan. The others are proposals: say if they do not work. The hours are guesses. Tell the engineer early if a job is taking much longer, because the plan has a list of things to cut when we fall more than two weeks behind.

### 1. Written question to Anthropic's sales team

Volunteers use a coding assistant that we pay for. Anthropic's published terms restrict paying for other people's use of its own command-line coding tool, so we do not plan to use that tool as our assistant unless Anthropic agrees in writing. Its toolkit for building our own assistant (the Agent SDK) looks allowed for products like ours, but the published pages do not address our exact case. We want both answers in writing.

Before you send it, read the section called "High-risk Areas" in Anthropic's Usage Policy (anthropic.com/legal/aup). We understand it lists employment uses such as screening candidates, and sets extra requirements for them. Check that yourself, and change question 4 if the policy says something different. Show the final text to the engineer, then send it through Anthropic's sales contact page. Keep the reply in writing and tell the engineer when it arrives.

Draft:

> Subject: Questions about using Claude in a technical-interview product
>
> Hello,
>
> We are building a product for technical interviews. A candidate spends about 90 minutes in a browser-based coding environment, working on a code repository with an AI coding assistant, and then people review the result. We would like written answers to four questions before we build further. In our design, we pay for the assistant's model usage on our own API key.
>
> 1. Can we offer the Claude Code command-line tool to candidates inside our product, with our API key paying for their usage? If not, is there a written agreement that would allow it?
> 2. Can we build our own coding assistant with the Claude Agent SDK, authenticate it with our API key, and give it to candidates? We read the Commercial Terms as allowing this and would like to confirm it.
> 3. How must we name the assistant? We understand it must not be called "Claude Code" and that "[our product name] Powered by Claude" is acceptable. Please confirm or correct this.
> 4. Part of our grading uses Claude, and the results help people judge candidates. We understand the Usage Policy lists employment uses among its high-risk areas. Does it apply to this use, and what would we need to do to comply?
>
> We would be glad to talk on a call if that is easier.
>
> [Your name, company, contact details]

### 2. Shortlist five base repositories

A base repository is the real open-source project that we plant flaws in. Use Part A of [repo-screening.md](repo-screening.md), which is written for you. Each repository takes about 10 minutes once you have found it, and you stop at the first "no".

To find candidates, go to GitHub's search, choose Repositories, and type one of these lines, adding a topic word such as `scheduler`, `invoice`, `inventory`, `rate limit`, `parser` or `config`:

```
language:Python stars:100..200 license:mit
language:Python stars:100..200 license:bsd-3-clause
language:Python stars:100..200 license:bsd-2-clause
```

Hand back five repositories, each with the one-paragraph note described at the end of the checklist. The engineer does Part B and picks three. The brackets in the checklist, such as [D10], point to the engineer's specs. You can ignore them.

### 3. A lawyer for the consent text

Find a lawyer who knows India's Digital Personal Data Protection Act, 2023. Ask what a review of the consent form and data note would cost and how long it takes. We want the review done before the first session.

We have not checked what the Act requires of us, including sending volunteers' prompts and code to an AI provider outside India. Treat our drafts as unchecked until the lawyer has read them. Questions to ask:

- Is our consent wording enough for what we record, and for sending prompts and code to an AI provider abroad?
- Is it acceptable to keep raw session data for 90 days after the Gate 1a report and then delete it? What do we need to say about backups?
- Do we need to name a contact for questions and complaints?
- Does it change anything that reviewers receive session material without names?
- What must we do when someone withdraws?

### 4. First drafts of four short documents

Write in plain language, one page each if you can. Use any tool you like, such as Google Docs, and send the drafts to the engineer. The engineer reviews them, then the lawyer reads the first two.

**Consent form and data note.** They can be one document. The consent must say:

- What is recorded: the terminal, the conversation with the assistant, the code (with a snapshot every 10 minutes), the engineer's notes from the walkthrough call (the call itself is not recorded), the survey answers and an experience band.
- Why: to test a grading method. It is not a hiring decision.
- Who sees it: the two founders, and reviewers who receive it without the volunteer's name.
- That their prompts and code are sent to an AI model provider outside India.
- How long it is kept: raw session data is deleted 90 days after the Gate 1a report, unless the volunteer agrees to longer. Reviewers delete their packets once they have sent their forms.
- How to withdraw: at any time, and their session data is then deleted.
- For the sessions where a volunteer uses their own Claude login: that the login stays under their control and is deleted from our server at the end.
- A request not to share the exercise with anyone.

Settle with the engineer how people sign. An e-signature tool or a reply saying "I agree" are the usual choices, and the lawyer should say which is enough.

**Fairness survey.** Sent after each session, about 5 minutes. The engineer's tools read two answers: a number from 1 to 5 for "How fair was this assessment?", and a free-text comment. You may add a few more questions, for example whether the brief was clear and whether the time was enough. Tell the engineer which ones you add, so the analysis can include them.

**Reviewer instructions.** Reviewers fill in two plain-text forms that the engineer's tools generate. Your job is the cover note that explains what to do, in this order:

1. Step 1, before they see our list of planted flaws: "How well did this person do the work?" from 1 to 5, with two sentences of reasons.
2. Step 2, sent only after their step 1 form is back. For every planted flaw and task: was it fixed or done (yes, partly or no)? A score from 0 to 3 for each rubric criterion, using the descriptions given. A rating for four working habits (observed, not observed or cannot tell, with a time pointer when observed). The minutes spent.

The four habits in plain words: running the tests after the assistant changed something; checking something the assistant claimed before relying on it; pushing back on a wrong answer from the assistant; and looking at evidence before deciding which side of a contradiction was right.

The note must also say that reviewers get no names and must not try to find out who did the work, that they must not discuss sessions with each other until every form is in, and that they delete the packet after sending their forms. Ask the engineer for a sample of the two forms so you can see how they read.

### 5. Recruit 8 to 10 volunteer engineers

We need about 12 sessions in total, and no fewer than 8. Some people can do two sessions, on different exercises.

- **Who:** working software engineers with a spread of experience, so we can see how results relate to it. They should read Python comfortably, be at home in a terminal, and be able to send an SSH public key (a small text file; the engineer will send a one-page how-to).
- **What you ask of each person:** 90 minutes without interruption for the session, about 15 minutes for a call within 48 hours afterwards, about 5 minutes for the survey, a signed consent form, and not sharing the exercise.
- **Own login:** find up to 3 volunteers who already have their own Claude account and are happy to sign in with it for their session. Those sessions are labelled and kept apart in the results.
- **Record in your private sheet** for each person: their code (C-01, C-02), experience band, the date consent was signed, the date their key arrived, and their session and call dates. The bands are not fixed. A starting suggestion is under 3 years, 3 to 7 years, and over 7 years. Agree them with the engineer.
- **Dates:** sessions cannot start until the engineer says the first exercise has passed its checks. Tell volunteers dates are provisional until then, and confirm each session two days ahead.

### 6. Recruit 3 or 4 reviewers

Experienced engineers who read Python well and are not otherwise involved in the pilot. Each gives about 5 to 7 hours in total, from early January to early February. Every session is judged by two reviewers, and every fourth session by three. Reviewers work alone and do not discuss sessions until every form is in. Record each reviewer's code (R1, R2) in your private sheet.

### 7. Run the logistics

For each session:

- **Two days before:** confirm the signed consent form and the volunteer's public key are in. Send the joining instructions and the session time. The engineer writes the technical part of the instructions once the server is ready, and you add the logistics. The engineer assigns an exercise the volunteer has not seen.
- **On the day:** the engineer is reachable for logistics only. If a volunteer asks you about the task itself, say: "I can't comment on the task. Use your judgment and note your assumptions in your findings." Nobody gets extra time. If the assistant or the model is down for more than 10 minutes, the engineer stops the session and you reschedule it with a different exercise. If a volunteer's connection drops, the clock keeps running, and the engineer may allow one pause of up to 10 minutes.
- **Within 48 hours after:** book the 15-minute walkthrough with the engineer and send the survey.
- **Reviewer packets:** when the engineer gives you a packet, send step 1 to the session's reviewers. Send step 2 to a reviewer only after their step 1 form is back. Never add a name to anything.
- **Your private sheet:** keep it up to date. It is the only place where names and codes meet.

## Decisions still open

Settle these with the engineer:

- Whether volunteers and reviewers are thanked or paid, and how.
- The company name and the assistant's name.
- How consent is signed.
- The experience bands.
- What volunteers get to see afterwards, if anything.

## How to hand work back

You do not need GitHub or the command line. Send documents to the engineer, who puts the final versions into the product or the repo. Anything with a person's name in it stays out of the repo.

## What the engineer is doing meanwhile

- Setting up the server and testing the scoring tools.
- Choosing and testing the coding assistant.
- Building the first exercise from your shortlist. The first session cannot happen until that exercise has passed its checks, and the engineer will tell you when it has.
