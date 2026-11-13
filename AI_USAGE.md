# AI Usage Disclosure

## Tools used

 ChatGPT

## What I used them for

I asked ChatGPT to review the assignment that i choose that is A and tell and explain brielfy in normal words what was it actually. And then I used it do research about how to protect the phone number as it was mentioned in the assignment. Then I asked it for an implementation plan. Then took a little help after designing the schema as some error was coming. I used it for generating the test files as I had no time to write them manually.

## What I wrote or decided myself

First I started to design the schema by seeing into the implentation plan, written the schema completly, then wrote `normalize.py` file by my own then for the `core.py` particularly `call_facts`.

## Where the AI was wrong or unhelpful

I would not say AI was unhelpful, but I could not accept every suggestion
without checking it. In an early version, the SQLite connections were not
closed properly, so a Windows test failed while cleaning up a temporary
database. The code was changed to close the connections, and the tests passed
after that. When an answer was not what I expected, asking a more specific
follow-up question helped me get a clearer explanation or fix.

## How I verified AI-generated code

When I was working in previous company as an Intern there aslo we used AI for coding , but my mentor there also used make us review the code ine by line and understand it and we should tell our mentor how exactly it used to work hence , I am used to it now I'll review the code line by line and undersatnd it before accepting it and if I dont understand any line i'll ask AI to tell me waht exactly that perticualr line of code is for. So here too, I used same style of verifying the code which was written by AI.
