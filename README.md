# TLDR

See [prompt.txt](prompt.txt) that prompts the LLM coding assistant to provide modification directives.

This tool requires you to work in a git repository:
```
$ git init .
$ git commit -m 'initial commit'
```
After installing this package, given a modification directives file named "fixes.txt":

```
(yourenvironment) $ vibemod-apply fixes.txt
```

# What's all this about?

Primarily for people using the el cheapo ($20/mo) LLMs to do vibe coding in Python*...

The race is on to make money coding fast enough to keep up with the increasing cost of coding assistants that actually "work" in some remotely reasonable sense.  In order to afford the $100/mo coding assistants, some people need to make more money.  They can start with $20/mo. Even if they have $100/mo to spend on chatbots it is wise, at this time, to subscribe to 4 major el cheapo LLMCA (Large Language Model Coding Assistant) contenders to explore exploit their rapidly changing capabilities.

The problem with any of the above is that the LLMs, while quite good at providing straight python syntax, are notoriously bad at the meta-syntax of code changes.  For example, they virtually never get the "unified diff" patch syntax correct.  They'll frequently try to provide an executable Python or shell script that does the patch to the Python source you're editing.  But then they'll make a mess of things like escaping quotes that are inside the Python source they're trying to convey to the patch script... etc.

So the aforelinked prompt.txt tells the LLMCA to use a simple, relatively bullet-proof meta-syntax.  Even then there are a wide variety of failure modes the LLMCAs fall into due to their idiosyncratic "psychology".  `modify_code.py` attempts to compensate for these failure modes as best it can based on a good deal of "eat your own dogfood" coding the author has done with this tool -- including using it to code vibemod itself.

\* The file modification directives are language independent.
