"""Scrape synthetic HTML into validated Pydantic models with one call. No network needed."""

from typing import Annotated, ClassVar

from pydantic import BaseModel, HttpUrl

import rastro

HTML = """
<ul>
  <li class="job"><h3>Platform engineer</h3><a href="/careers/1">Apply</a>
    <span class="salary">5200</span></li>
  <li class="job"><h3>Product designer</h3><a href="/careers/2">Apply</a></li>
</ul>
"""


class Offer(BaseModel):
    __records__: ClassVar[str] = "li.job"
    title: Annotated[str, rastro.css("h3::text")]
    url: Annotated[HttpUrl, rastro.css("a::attr(href)")]
    salary: Annotated[int | None, rastro.css(".salary::text")] = None


def main() -> None:
    result = rastro.scrape(HTML, Offer, url="https://acme.example/careers")
    print(result.status.value)
    for offer in result.items:
        print(offer.title, offer.url, offer.salary)


if __name__ == "__main__":
    main()
