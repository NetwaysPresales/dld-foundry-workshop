# DLD Microsoft Foundry Workshop

Build, in your own Azure subscription, a small team of AI agents that reads a tenant's rent-increase
email, checks Dubai's rental rules with citations, drafts the reply, books a mediation appointment,
and waits for your approval before anything is sent.

The rental documents summarise Dubai rules for training only. Not legal advice.

## Open the notebooks in Google Colab

Run them in order. Each opens in Colab from the link; the first cell downloads the code and asks
you to sign in to Azure with a code.

| Notebook | Open |
|---|---|
| 00 Workshop Environment | [Open in Colab](https://colab.research.google.com/github/NetwaysPresales/dld-foundry-workshop/blob/main/workshop/00_Workshop_Environment.ipynb) |
| 01 Knowledge RAG and Foundry IQ | [Open in Colab](https://colab.research.google.com/github/NetwaysPresales/dld-foundry-workshop/blob/main/workshop/01_Knowledge_RAG_and_Foundry_IQ.ipynb) |
| 02 Safety Identity and Evaluation | [Open in Colab](https://colab.research.google.com/github/NetwaysPresales/dld-foundry-workshop/blob/main/workshop/02_Safety_Identity_and_Evaluation.ipynb) |
| 03 Models and Model Routing | [Open in Colab](https://colab.research.google.com/github/NetwaysPresales/dld-foundry-workshop/blob/main/workshop/03_Models_and_Model_Routing.ipynb) |
| 04 Agent Tools MCP and A2A | [Open in Colab](https://colab.research.google.com/github/NetwaysPresales/dld-foundry-workshop/blob/main/workshop/04_Agent_Tools_MCP_and_A2A.ipynb) |
| 05 Microsoft Agent Framework and Hosted Agents | [Open in Colab](https://colab.research.google.com/github/NetwaysPresales/dld-foundry-workshop/blob/main/workshop/05_Microsoft_Agent_Framework_and_Hosted_Agents.ipynb) |
| 06 Multi Agent Orchestration and Events | [Open in Colab](https://colab.research.google.com/github/NetwaysPresales/dld-foundry-workshop/blob/main/workshop/06_Multi_Agent_Orchestration_and_Events.ipynb) |
| 07 Observability Governance and Delivery | [Open in Colab](https://colab.research.google.com/github/NetwaysPresales/dld-foundry-workshop/blob/main/workshop/07_Observability_Governance_and_Delivery.ipynb) |

You need: a Google account, an Azure subscription where you're Owner (or Contributor + Role Based
Access Control Administrator on a resource group), and for the mail and calendar steps a Microsoft
365 Copilot licence. Notebook 00 checks all of this for you.

**Cleanup:** run the last cell of notebook 07 to delete everything and stop charges.
