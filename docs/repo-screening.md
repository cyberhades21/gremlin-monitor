# Choosing a base repo: first-pass screening

For the co-founder (part A, no coding) and the engineer (part B, needs a machine). Every item traces to a decision or spec, named in brackets. Reject a repo at the first "no". Do not spend time on the rest.

Aim for a shortlist of 5 repos. The engineer picks 3 for the pilot, varying the domain.

The size and count figures below are starting figures, a judgment and not a rule in any spec. Adjust them after the first package is built.

## Part A: checks on GitHub, about 10 minutes per repo

1. **Licence is MIT, BSD-2-Clause or BSD-3-Clause.** It shows in the sidebar, and the LICENSE file at the top of the repo says the same. Apache-2.0, GPL, LGPL, MPL, Unlicense, "no licence" and custom licences are all a no. [D10, spec 01 "Licence notices"]
2. **Python, with pytest tests.** A `tests` folder holds files named `test_something.py`, and the repo's checks (look in `.github/workflows`) run `pytest`. [D10]
3. **A niche project.** Between about 100 and 200 stars. Search its name on the web. If you find many tutorials, blog posts or videos, it is a no, because the assistant may know it. [D10, T5]
4. **A sensible size.** The main package has about 8 to 40 Python files, and none is huge. Under 1,000 lines for the whole package is too little to plant 6 to 10 flaws in. Over about 5,000 is too much to read in 90 minutes. [spec 02 sizing guide, spec 01 session time]
5. **Written explanations exist.** The README or a `docs` folder says how things behave: rules, limits, formats, what happens at the edges. Flaws that contradict the documentation need documentation to contradict. [D1, spec 02]
6. **Plain to understand.** Someone with no special background grasps what it does in 5 minutes. Think scheduling, invoices, inventory, rate limits, parsing or config loading. Not security tools, medical, banking or anything about people's personal data.
7. **Self-contained.** The README's install is a plain `pip install`, with about 10 or fewer direct dependencies. No database server, browser, GPU, cloud account or API key. The tests do not call the internet. [spec 01 rule 4, "Offline"]
8. **Not a clone of something famous,** and not from a well-known company or project.

Nice to have, not required: the maintainer still answers issues, because candidates may later report real bugs upstream.

## Part B: the engineer's check, about 1 hour per repo

1. Clone at one commit and record it. Hash the LICENSE file. Count the lines: `git ls-files '*.py' | xargs wc -l`. [spec 01 `package.yaml`]
2. The test suite passes at that commit on a clean machine, in under 2 minutes, three times in a row. Then it passes in a container with the network off. [V2]
3. Every dependency can be pinned with `==`, installs as a wheel without compiling, and has a permissive licence. The wheels fit in the dependency cache. [spec 01 rules 10 and 11]
4. It runs on Python 3.12.
5. Count the places where the documentation states a rule that the code enforces. About 15 or more means there is room for 6 to 10 flaws, with about 3 contradictions. [spec 02]
6. At least 3 modules with clear separate jobs, so there is a feature to add and an architecture to explain. [spec 01 `session.tasks`]
7. Nothing in the repo holds a secret, a person's data or a call home.
8. Write the one-paragraph note below.

## The note for each shortlisted repo

- URL and commit, licence, stars, lines of Python, number of test files.
- One sentence on what it does.
- Time the test suite takes.
- The doubts: anything you were unsure about.

## What happens next

The engineer picks 3. Claude proposes each flaw and its hidden test, one at a time, and the engineer approves or rejects each one. [D11] Log the time spent as stage `other` in `validation/hours.csv`. The name and URL of the repo go only in `package.yaml`. Nothing a candidate sees may mention them. [spec 01 rule 6]
