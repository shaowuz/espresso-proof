# Writing examples

These are synthetic examples, not excerpts from a reviewed paper or evidence of
measured skill performance. Preserve their distinction between improved exposition,
a justified elementary repair, a false claim, and an unavailable source.

## 1. Move an argument out of a statement and complete it

### Input

Let V be a vector space over a field k and let p: V -> V be linear with p^2 = p.
Put K = {v in V : p(v) = 0} and I = p(V). Call the pair (K, I) a splitting signature.

Theorem. V = K direct-sum I, since v = (v - p(v)) + p(v), and the intersection
obviously vanishes; applying this, every v has a unique decomposition. The
splitting is clear because p is idempotent.

### Revised manuscript

```latex
Let $V$ be a vector space over a field $k$, and let $p: V \to V$ be a linear map
satisfying $p^2=p$.

\begin{proposition}
We have $V=\ker(p)\oplus\mathrm{im}(p)$. In particular, every vector has a unique
expression as a sum of a vector in $\ker(p)$ and a vector in $\mathrm{im}(p)$.
\end{proposition}

\begin{proof}
For $v\in V$, write $v=(v-p(v))+p(v)$. The first summand lies in $\ker(p)$, since
$p(v-p(v))=p(v)-p^2(v)=0$, and the second lies in $\mathrm{im}(p)$. Thus the two
subspaces span V.

If $w\in\ker(p)\cap\mathrm{im}(p)$, write $w=p(u)$ for some $u\in V$. Then
$0=p(w)=p^2(u)=p(u)=w$. The intersection is therefore zero, which also gives
uniqueness of the decomposition.
\end{proof}
```

Use the manuscript's existing proposition/proof environments and notation;
the snippet assumes those environments are already available. Do not change
document packages merely to match the example.

### Author review note

- Replaced the unnecessary name splitting signature and the local K/I notation
  with standard kernel/image notation; the input already defines the same spaces.
- Moved the argument into a proof and supplied the omitted intersection argument.
  The repair uses only the stated linearity and idempotence assumptions.
- Preserved the uniqueness consequence. No finite-dimensionality, characteristic,
  or extra hypothesis was added.

### Audit

The target is not just fewer words. The original decomposition identity is useful
and remains in the proof; the repeated assertion of obviousness is removed.
The actual inference establishing the zero intersection is now inspectable.
This is a located qualitative comparison, not a measured before/after score.

## 2. Do not polish a false theorem into an apparently complete proof

### Input

Theorem T1. Every continuous function f: (0,1) -> R is bounded. This follows
from compactness.

### Revised manuscript passage

The proposed statement T1 is false. The function f(x)=1/x is continuous on
(0,1) and unbounded there: for every M>0, the point x=1/(M+1) lies in (0,1)
and satisfies f(x)=M+1>M.

### Author review note and audit

- Record issue G1 as a refuted claim with the counterexample above. Do not leave
  T1 labeled as an established theorem or describe the defect as missing prose.
- Do not silently replace (0,1) by [0,1], impose a new compactness hypothesis,
  or label the original false statement as a conjecture after refuting it.
- A replacement statement needs its own identity and an explicit record of the
  changed domain or assumptions. The original contribution remains unestablished.
- Keep the original T1 and the counterexample in the review history. Do not
  count G1 as an active obligation to prove the false statement.

## 3. Keep an unavailable citation inspectable

### Input

For the objects X and Y already defined in the draft, the author invokes
[R, Theorem 4.2] to conclude that a specified map f: X -> Y is an isomorphism.
The source R has not been supplied and cannot be retrieved in the current review.
This citation label is fictional and used only for this example.

### Revised manuscript passage

The next step requires f to be an isomorphism. The draft invokes [R, Theorem 4.2]
for this assertion; its applicability remains unverified (issue C1).
Conditional on this assertion, continue with the existing subsequent argument.

### Author review note and audit

- Record C1 as an unverified source application, not a confirmed citation misuse.
- The required conclusion is that this particular f is an isomorphism, not merely
  that some isomorphism X -> Y exists. Check the source's actual objects,
  hypotheses, conclusion, version, and locator when it becomes available.
- Record the affected results and make their conditional status agree across
  the manuscript. A short pointer in the proof may link to these details.
- If this is the only supporting citation in scope, checked coverage is 0/1
  and citation misuse rate is N/A. Do not report either 0% or 100% misuse.
- Do not invent the missing theorem statement or claim the citation was checked.

## 4. Distinguish a shared lemma from circular reasoning

Edges A -> B mean B uses A. The dependencies A -> B, A -> C, B -> D, C -> D
are acyclic and permit a shared input A. Their underlying undirected cycle is
not a proof defect.

The dependencies A -> B and B -> A instead need an independent justification
or an explicitly valid proof principle. Moving B earlier in the paper does not
repair the reasoning. Check scoped assumptions, induction, and assumption
discharge before classifying a dependency as unjustified.

## Useful explanations are not automatic defects

- Keep an explanation of what a difficult definition measures when its formula
  alone does not orient the intended reader. Remove a sentence that only repeats
  the same assertion with no mathematical or expository function.
- Keep parallel theorem statements when their wording makes changed hypotheses
  easy to compare. Do not collapse them merely to improve a repetition count.
- Keep an if-then condition or an equivalence inside a statement when it specifies
  the claim. Move a sentence explaining why the implication is true to its proof.
- Keep the construction of a specific map when needed to state the result;
  an arbitrary quantified map needs its declared type, not an invented formula.
